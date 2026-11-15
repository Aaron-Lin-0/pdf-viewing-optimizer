"""Tests for BOOX .note to vector PDF pipeline adapter with portable programmatic fixtures."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from note_to_pdf import (
    _decode_varint,
    _parse_shape_container,
    inspect_note,
    find_renderer,
    convert_note_to_pdf,
    allocate_audit_job_dir,
    extract_toc,
    inject_toc,
    count_toc_nodes,
    MAX_ARCHIVE_ENTRIES,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_DEPTH,
)
from pdf_runtime import TimeBudgetExceeded, DiskBudgetExceeded, filehash


def _encode_varint(v):
    out = []
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def _encode_field(num, wire, data):
    tag = _encode_varint((num << 3) | wire)
    if wire == 2:
        return tag + _encode_varint(len(data)) + data
    elif wire == 0:
        return tag + _encode_varint(data)
    return tag + data


def create_test_note(
    dest_path,
    pages=("p1",),
    shapes=None,
    templates=None,
    content_type="geo_layout",
    corrupt_proto=False,
    corrupt_shape=False,
    corrupt_meta_json=False,
    depth3_zip=False,
    omit_note_info=False,
    toc_items=None,
    extra_files=None,
):
    """Programmatically generate a minimal valid or intentionally flawed BOOX .note archive."""
    import zipfile

    dest_path = Path(dest_path)
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w") as zf:
        if not omit_note_info:
            if corrupt_meta_json:
                meta = _encode_field(20, 2, b"NOT_VALID_JSON{")
            elif corrupt_proto:
                # Intentionally incomplete varint / truncated field
                meta = b"\x80\x80\x80"
            else:
                meta = _encode_field(20, 2, json.dumps({"pageNameList": list(pages)}).encode("utf-8"))
            zf.writestr("note/pb/note_info", _encode_field(1, 2, meta))

        # virtual/doc format gate
        zf.writestr(
            "virtual/doc/pb/doc",
            _encode_field(9, 2, json.dumps({"contentType": content_type}).encode("utf-8")),
        )

        # virtual/page placement
        vp_items = []
        for p in pages:
            vp_data = _encode_field(1, 2, p.encode("utf-8")) + _encode_field(
                6, 2, json.dumps({"left": 0.0, "top": 0.0, "right": 1860.0, "bottom": 2480.0}).encode("utf-8")
            )
            vp_items.append(_encode_field(1, 2, vp_data))
        zf.writestr("virtual/page/pb/page", b"".join(vp_items))

        # shapes
        if shapes is not None or corrupt_shape or depth3_zip:
            sz_buf = io.BytesIO()
            with zipfile.ZipFile(sz_buf, "w") as sz:
                if depth3_zip:
                    nested3_buf = io.BytesIO()
                    with zipfile.ZipFile(nested3_buf, "w") as sz3:
                        sz3.writestr("deep.txt", b"too deep")
                    sz.writestr("nested.zip", nested3_buf.getvalue())
                elif corrupt_shape:
                    sz.writestr("p1#s1", b"\x0a\x50truncated_shape")
                elif shapes:
                    shape_bytes_list = []
                    for s in shapes:
                        s_body = b""
                        s_body += _encode_field(1, 2, s.get("uuid", "shape-1").encode("utf-8"))
                        s_body += _encode_field(6, 0, s.get("layer", 0))
                        bbox = s.get("bbox", {"left": 10.0, "top": 10.0, "right": 200.0, "bottom": 50.0})
                        s_body += _encode_field(7, 2, json.dumps(bbox).encode("utf-8"))
                        s_body += _encode_field(12, 0, s.get("pen_type", 6))
                        if "text_plain" in s:
                            s_body += _encode_field(10, 2, s["text_plain"].encode("utf-8"))
                        shape_bytes_list.append(_encode_field(1, 2, s_body))
                    sz.writestr("p1#s1", b"".join(shape_bytes_list))
                else:
                    sz.writestr("p1#s1", b"")
            zf.writestr("shape/p1#g1#1000.zip", sz_buf.getvalue())

        # templates
        if templates:
            for i, tmpl in enumerate(templates):
                zf.writestr(
                    f"document/p1/template/json/t{i}.template_json",
                    json.dumps(tmpl).encode("utf-8"),
                )

        # table of contents (TOC)
        if toc_items is not None:
            toc_bytes_list = []
            for item in toc_items:
                item_body = b""
                if "id" in item:
                    item_body += _encode_field(1, 2, item["id"].encode("utf-8"))
                if "page_uuid" in item:
                    item_body += _encode_field(4, 2, item["page_uuid"].encode("utf-8"))
                if "title" in item:
                    item_body += _encode_field(5, 2, item["title"].encode("utf-8"))
                if "page_idx" in item:
                    item_body += _encode_field(6, 0, item["page_idx"])
                if item.get("parent_id"):
                    item_body += _encode_field(8, 2, item["parent_id"].encode("utf-8"))
                toc_bytes_list.append(_encode_field(1, 2, item_body))
            zf.writestr("toc/pb/00000000-0000-0000-0000-000000000001", b"".join(toc_bytes_list))

        if extra_files:
            for fname, content in extra_files.items():
                zf.writestr(fname, content)

    dest_path.write_bytes(buf.getvalue())
    return dest_path


class TestNoteToPdf(unittest.TestCase):
    def test_find_renderer(self):
        renderer = find_renderer()
        self.assertTrue(renderer.is_file())
        if os.environ.get("BOOX_NOTES_RENDERER"):
            self.assertEqual(renderer, Path(os.environ["BOOX_NOTES_RENDERER"]).resolve())
        self.assertTrue(renderer.is_absolute())

    def test_decode_varint_and_shape_bounds(self):
        # 1. _decode_varint with max_pos
        data = b"\x80\x80\x01\x00"
        # Reading with max_pos=2 should fail because varint is truncated at pos 2
        with self.assertRaises(ValueError) as ctx:
            _decode_varint(data, 0, max_pos=2)
        self.assertIn("Truncated varint", str(ctx.exception))

        # Reading past max_pos directly raises ValueError
        with self.assertRaises(ValueError):
            _decode_varint(data, 2, max_pos=2)

        # 2. _parse_shape_container bounds checking
        # Build shape container with tag field 1 wire 2 length 10, but only 4 bytes of data
        bad_container = _encode_varint((1 << 3) | 2) + _encode_varint(10) + b"\x08\x01\x00\x00"
        with self.assertRaises(ValueError):
            _parse_shape_container(bad_container)

        # Truncated varint inside shape record (spos reaching end_pos)
        truncated_in_shape = _encode_varint((1 << 3) | 2) + _encode_varint(3) + b"\x80\x80\x80"
        with self.assertRaises(ValueError):
            _parse_shape_container(truncated_in_shape)

    def test_inspect_note_programmatic_compatible(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            note_file = Path(tmpdir) / "c.note"
            create_test_note(
                note_file,
                pages=["p1"],
                shapes=[{"pen_type": 6, "uuid": "txt-1", "text_plain": "Hello"}],
                templates=[{"resName": "", "displayFillColor": 0, "fillPath": {"empty": True}}],
            )

            info = inspect_note(note_file)
            self.assertEqual(info["page_count"], 1)
            self.assertEqual(info["shapes_total"], 1)
            self.assertEqual(info["pen_counts"].get(6), 1)
            self.assertTrue(info["strictly_compatible"])
            self.assertEqual(len(info["unsupported_reasons"]), 0)
            self.assertIsNotNone(info["renderer_sha256"])
            self.assertEqual(len(info["renderer_sha256"]), 64)

    def test_inspect_note_multiple_metadata_records(self):
        """Test Finding 1 fix: note_info with multiple records in field 1 where only one carries field 20."""
        import zipfile

        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            note_file = Path(tmpdir) / "multi_meta.note"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                # First record in field 1: viewport/pen info, NO field 20
                rec1 = _encode_field(9, 5, struct.pack("<f", 2.0)) + _encode_field(11, 2, b"{}")
                # Second record in field 1: contains field 20 pageNameList
                rec2 = _encode_field(20, 2, json.dumps({"pageNameList": ["pageA", "pageB"]}).encode("utf-8"))
                # Outer note_info with repeated field 1
                note_info = _encode_field(1, 2, rec1) + _encode_field(1, 2, rec2)
                zf.writestr("note/pb/note_info", note_info)
                zf.writestr(
                    "virtual/doc/pb/doc",
                    _encode_field(9, 2, json.dumps({"contentType": "geo_layout"}).encode("utf-8")),
                )
                vp_data = _encode_field(1, 2, b"pageA") + _encode_field(
                    6, 2, json.dumps({"left": 0.0, "top": 0.0, "right": 1860.0, "bottom": 2480.0}).encode("utf-8")
                )
                zf.writestr("virtual/page/pb/page", _encode_field(1, 2, vp_data))

            note_file.write_bytes(buf.getvalue())
            info = inspect_note(note_file)
            self.assertEqual(info["page_count"], 2)
            self.assertEqual(info["pages"], ["pagea", "pageb"])

    def test_inspect_note_real_notebook_if_present(self):
        selected = os.environ.get('PDF_NOTE_TEST_INPUT')
        if not selected:
            self.skipTest('Set PDF_NOTE_TEST_INPUT to opt into a private notebook preflight.')
        real_note = Path(selected).expanduser()
        self.assertTrue(real_note.is_file(), 'PDF_NOTE_TEST_INPUT must name an existing notebook file.')
        before = filehash(real_note)
        info = inspect_note(real_note)
        self.assertGreater(info['page_count'], 0)
        self.assertEqual(filehash(real_note), before)

    def test_inspect_note_programmatic_unsupported(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            note_file = Path(tmpdir) / "u.note"
            create_test_note(
                note_file,
                pages=["p1", "p2"],
                shapes=[
                    {"pen_type": 22, "uuid": "charcoal-1"},  # charcoal pen
                    {"pen_type": 6, "uuid": "text-1", "text_plain": "Hello"},
                ],
                templates=[{"resName": "grid.svg", "displayFillColor": -1}],  # active template
            )

            info = inspect_note(note_file)
            self.assertEqual(info["page_count"], 2)
            self.assertEqual(info["shapes_total"], 2)
            self.assertIn(22, info["pen_counts"])
            self.assertFalse(info["strictly_compatible"])
            # Must reject both pen 22 and active template
            self.assertTrue(any("Pen type 22" in r for r in info["unsupported_reasons"]))
            self.assertTrue(any("Active non-blank template" in r for r in info["unsupported_reasons"]))

    def test_inspect_note_limits(self):
        import zipfile

        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)

            # 1. Entry count limit (> 50,000 entries)
            entry_bomb = tmp / "entry_bomb.note"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                # Add MAX_ARCHIVE_ENTRIES + 5 empty entries
                for i in range(MAX_ARCHIVE_ENTRIES + 5):
                    zf.writestr(f"f_{i}.txt", b"")
            entry_bomb.write_bytes(buf.getvalue())
            with self.assertRaises(ValueError) as ctx:
                inspect_note(entry_bomb)
            self.assertIn("exceeds limit", str(ctx.exception))

            # 2. Uncompressed bytes limit (> 500 MB)
            size_bomb = tmp / "size_bomb.note"
            buf2 = io.BytesIO()
            with zipfile.ZipFile(buf2, "w") as zf:
                meta = _encode_field(20, 2, json.dumps({"pageNameList": ["p1"]}).encode("utf-8"))
                zf.writestr("note/pb/note_info", _encode_field(1, 2, meta))
            raw = bytearray(buf2.getvalue())
            cd_idx = raw.find(b"PK\x01\x02")
            # Set uncompressed size in central directory exceeding MAX_UNCOMPRESSED_BYTES
            raw[cd_idx + 24 : cd_idx + 28] = struct.pack("<I", MAX_UNCOMPRESSED_BYTES + 1024)
            size_bomb.write_bytes(raw)
            with self.assertRaises(ValueError) as ctx:
                inspect_note(size_bomb)
            self.assertIn("exceed", str(ctx.exception))

            # 3. Nested ZIP depth (> 2)
            depth_note = tmp / "depth3.note"
            create_test_note(depth_note, depth3_zip=True)
            with self.assertRaises(ValueError) as ctx:
                inspect_note(depth_note)
            self.assertIn(f"depth 3 > {MAX_ZIP_DEPTH}", str(ctx.exception))

    def test_inspect_note_fail_closed_on_corrupt(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)

            # Non-existent file
            with self.assertRaises(FileNotFoundError):
                inspect_note(tmp / "no.note")

            # Non-zip file
            non_zip = tmp / "non_zip.note"
            non_zip.write_bytes(b"NOT A ZIP CONTENT")
            with self.assertRaises(ValueError):
                inspect_note(non_zip)

            # Missing note_info
            no_info = tmp / "no_info.note"
            create_test_note(no_info, omit_note_info=True)
            with self.assertRaises(ValueError) as ctx:
                inspect_note(no_info)
            self.assertIn("missing required note_info", str(ctx.exception).lower())

            # Corrupt protobuf in note_info
            corrupt_pb = tmp / "corrupt_proto.note"
            create_test_note(corrupt_pb, corrupt_proto=True)
            with self.assertRaises(ValueError):
                inspect_note(corrupt_pb)

            # Corrupt metadata JSON in note_info
            corrupt_json = tmp / "corrupt_json.note"
            create_test_note(corrupt_json, corrupt_meta_json=True)
            with self.assertRaises(ValueError):
                inspect_note(corrupt_json)

            # Corrupt shape container protobuf
            corrupt_shape = tmp / "corrupt_shape.note"
            create_test_note(corrupt_shape, corrupt_shape=True)
            with self.assertRaises(ValueError):
                inspect_note(corrupt_shape)

    def test_allocate_audit_job_dir(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            base = Path(tmpdir) / "audit"
            dir1 = allocate_audit_job_dir(base, "sample")
            dir2 = allocate_audit_job_dir(base, "sample")

            # Must be distinct directories
            self.assertNotEqual(dir1, dir2)
            self.assertTrue(dir1.is_dir())
            self.assertTrue(dir2.is_dir())

            # Format check: note-<stem>-<timestamp_with_microsec>-<pid>-<token>
            self.assertTrue(dir1.name.startswith("note-sample-"))
            parts = dir1.name.split("-")
            # Must have at least stem, date, time, microsec, pid, token
            self.assertGreaterEqual(len(parts), 6)
            self.assertEqual(parts[-2], str(os.getpid()))
            self.assertEqual(len(parts[-1]), 8)  # 4-byte hex token

    def test_collision_refusal(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "s.note"
            create_test_note(note_file)

            out_pdf = tmp / "out.pdf"
            out_pdf.write_bytes(b"%PDF-1.4 dummy")

            with self.assertRaises(FileExistsError):
                convert_note_to_pdf(
                    note_path=note_file,
                    output_pdf=out_pdf,
                    audit_dir=tmp / "a",
                )

            # Collision on existing report .json
            out_pdf.unlink()
            report_json = out_pdf.with_suffix(".json")
            report_json.write_text("{}", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                convert_note_to_pdf(
                    note_path=note_file,
                    output_pdf=out_pdf,
                    audit_dir=tmp / "a",
                )

    def test_resource_budget_enforcement(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "s.note"
            create_test_note(note_file)
            out_pdf = tmp / "out.pdf"
            audit_dir = tmp / "a"

            # 1. Time budget initialization before preflight: 0 second budget must fail immediately
            with self.assertRaises(TimeBudgetExceeded):
                convert_note_to_pdf(
                    note_path=note_file,
                    output_pdf=out_pdf,
                    audit_dir=audit_dir,
                    time_budget_seconds=0.000001,
                )

            # 2. Disk budget check before publishing: even with --skip-optimize, disk budget of 10 bytes must fail
            with self.assertRaises(DiskBudgetExceeded):
                convert_note_to_pdf(
                    note_path=note_file,
                    output_pdf=out_pdf,
                    audit_dir=audit_dir,
                    skip_optimize=True,
                    disk_budget_bytes=10,
                )
            # Output PDF must NOT have been published
            self.assertFalse(out_pdf.exists())

    def test_renderer_build_hash_recorded(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "s.note"
            create_test_note(note_file)
            audit_dir = tmp / "a"

            report = convert_note_to_pdf(
                note_path=note_file,
                audit_dir=audit_dir,
                skip_optimize=True,
                strict=True,
            )
            self.assertEqual(report["status"], "success")
            renderer_sha = report["renderer"].get("sha256")
            self.assertIsNotNone(renderer_sha)
            self.assertTrue(re.fullmatch(r"[0-9a-fA-F]{64}", renderer_sha))
            self.assertEqual(report["preflight"].get("renderer_sha256"), renderer_sha)

            # Verify md report contains hash
            job_dir = Path(report["audit_job_dir"])
            md_content = (job_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Renderer Build SHA-256", md_content)
            self.assertIn(renderer_sha, md_content)

    def test_renderer_rejection_audit(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            out_pdf = tmp / "out.pdf"
            audit_dir = tmp / "a"

            # Programmatic note with active template asset
            reject_note = tmp / "r.note"
            create_test_note(
                reject_note,
                pages=["p1"],
                templates=[{"resName": "grid_template.svg", "displayFillColor": -1}],
                extra_files={"resource/template/grid_template.svg": b"<svg></svg>"},
            )

            report = convert_note_to_pdf(
                note_path=reject_note,
                output_pdf=out_pdf,
                audit_dir=audit_dir,
                strict=True,
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["conversion_verdict"], "rejected")
            self.assertIn("unsupported feature in strict mode", report["reason"])
            # Output PDF must not be published
            self.assertFalse(out_pdf.exists())

            # Audit report must be generated
            job_dir = Path(report["audit_job_dir"])
            report_json = job_dir / "report.json"
            report_md = job_dir / "report.md"
            self.assertTrue(report_json.is_file())
            self.assertTrue(report_md.is_file())

            loaded = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertEqual(loaded["conversion_verdict"], "rejected")
            self.assertIn("grid_template.svg", report["reason"])

    def test_skip_optimize_flow(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "v.note"
            create_test_note(
                note_file,
                pages=["p1"],
                shapes=[{"pen_type": 6, "uuid": "txt-1", "text_plain": "Hello world"}],
            )
            out_pdf = tmp / "direct.pdf"
            audit_dir = tmp / "a"

            report = convert_note_to_pdf(
                note_path=note_file,
                output_pdf=out_pdf,
                audit_dir=audit_dir,
                page=1,
                skip_optimize=True,
                strict=True,
            )

            self.assertEqual(report["status"], "success")
            self.assertEqual(report["conversion_verdict"], "converted")
            self.assertEqual(report["optimization_verdict"], "skipped")
            self.assertTrue(out_pdf.is_file())
            self.assertGreater(out_pdf.stat().st_size, 500)
            self.assertTrue(out_pdf.with_suffix(".json").is_file())
            self.assertTrue(out_pdf.with_suffix(".md").is_file())

    def test_end_to_end_conversion_and_optimization(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "ve2e.note"
            create_test_note(
                note_file,
                pages=["p1"],
                shapes=[{"pen_type": 6, "uuid": "txt-1", "text_plain": "Hello world"}],
            )
            out_pdf = tmp / "opt.pdf"
            audit_dir = tmp / "a"

            orig_sha = inspect_note(note_file)["sha256"]

            report = convert_note_to_pdf(
                note_path=note_file,
                output_pdf=out_pdf,
                audit_dir=audit_dir,
                page=1,
                strict=True,
            )

            self.assertEqual(report["status"], "success")
            self.assertEqual(report["conversion_verdict"], "converted")
            self.assertIn(report["optimization_verdict"], ["smaller-lossless", "retained direct converted PDF"])
            self.assertTrue(out_pdf.is_file())

            # Verify source immutability
            new_sha = inspect_note(note_file)["sha256"]
            self.assertEqual(orig_sha, new_sha)

            # Verify reports
            self.assertTrue(out_pdf.with_suffix(".json").is_file())
            self.assertTrue(out_pdf.with_suffix(".md").is_file())
            md_content = out_pdf.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("BOOX .note to Vector PDF Pipeline Report", md_content)
            self.assertIn("Output PDF is a static vector viewing copy", md_content)

    def test_remaining_budget_passed_to_optimize_quick(self):
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "bchk.note"
            create_test_note(note_file)
            out_pdf = tmp / "opt.pdf"
            audit_dir = tmp / "a"

            captured_args = {}

            def fake_optimize_quick(**kwargs):
                captured_args.update(kwargs)
                kwargs["destination"].write_bytes(b"%PDF-1.4 optimized")
                kwargs["destination"].with_suffix(".json").write_text(
                    json.dumps({"status": "optimized", "classification": "smaller-lossless"}),
                    encoding="utf-8",
                )

            with patch("note_to_pdf.optimize_quick", side_effect=fake_optimize_quick):
                convert_note_to_pdf(
                    note_path=note_file,
                    output_pdf=out_pdf,
                    audit_dir=audit_dir,
                    time_budget_seconds=100.0,
                    disk_budget_bytes=10_000_000,
                    strict=True,
                )

            self.assertIn("time_budget_seconds", captured_args)
            self.assertIn("disk_budget_bytes", captured_args)
            # Remaining time must be less than original 100.0 seconds
            self.assertLess(captured_args["time_budget_seconds"], 100.0)
            self.assertGreater(captured_args["time_budget_seconds"], 0.0)
            # Remaining disk must be less than or equal to original 10,000,000 bytes
            self.assertLessEqual(captured_args["disk_budget_bytes"], 10_000_000)

    def test_verification_chunking_threshold_and_batches(self):
        """Test that PDF.js verification is chunked into <=30 page sequential batches when pages > 50."""
        from quick_lossless import (
            verify_pdfjs_chunked,
            VERIFICATION_CHUNK_PAGE_THRESHOLD,
            VERIFICATION_CHUNK_SIZE,
        )

        # 1. Page count below or equal to threshold (50) -> single unchunked call
        mock_pdfjs = MagicMock(return_value=[{"page": i, "ok": True} for i in range(1, 51)])
        with patch("viewing_copy.verify_pdfjs", mock_pdfjs):
            results = verify_pdfjs_chunked(
                source="dummy_src.pdf",
                candidate="dummy_cand.pdf",
                pages=50,
                threshold=VERIFICATION_CHUNK_PAGE_THRESHOLD,
                chunk_size=VERIFICATION_CHUNK_SIZE,
            )
            self.assertEqual(len(results), 50)
            mock_pdfjs.assert_called_once()
            args, _ = mock_pdfjs.call_args
            self.assertEqual(args[2], 50)

        # 2. Page count above threshold (91 pages) -> 4 sequential batches
        batch_calls = []

        def fake_verify_batch(source, candidate, count, **kwargs):
            self.assertIsInstance(count, list)
            self.assertLessEqual(len(count), VERIFICATION_CHUNK_SIZE)
            batch_calls.append(list(count))
            return [{"page": p, "ok": True} for p in count]

        mock_batch = MagicMock(side_effect=fake_verify_batch)
        mock_budget = MagicMock()

        with patch("viewing_copy.verify_pdfjs", mock_batch):
            results_91 = verify_pdfjs_chunked(
                source="dummy_src.pdf",
                candidate="dummy_cand.pdf",
                pages=91,
                budget=mock_budget,
                threshold=VERIFICATION_CHUNK_PAGE_THRESHOLD,
                chunk_size=VERIFICATION_CHUNK_SIZE,
            )
            self.assertEqual(len(results_91), 91)
            self.assertEqual([r["page"] for r in results_91], list(range(1, 92)))
            self.assertEqual(len(batch_calls), 4)
            # Batch 1: 1..30 (30 pages)
            self.assertEqual(batch_calls[0], list(range(1, 31)))
            # Batch 2: 31..60 (30 pages)
            self.assertEqual(batch_calls[1], list(range(31, 61)))
            # Batch 3: 61..90 (30 pages)
            self.assertEqual(batch_calls[2], list(range(61, 91)))
            # Batch 4: 91 (1 page)
            self.assertEqual(batch_calls[3], [91])

            # Verify budget checkpoint called for each batch
            self.assertEqual(mock_budget.check.call_count, 4)

    def test_verification_chunking_with_real_pdfjs(self):
        """Integration test: real PDF.js child processes succeed on chunked non-1-based page batches."""
        from quick_lossless import verify_pdfjs_chunked
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            source = tmp / "src.pdf"
            candidate = tmp / "cand.pdf"

            writer = PdfWriter()
            for _ in range(4):
                writer.add_blank_page(width=100, height=100)
            with source.open("wb") as f:
                writer.write(f)
            shutil.copyfile(source, candidate)

            # Test chunking with threshold=2, chunk_size=2 on 4 pages -> 2 chunks of 2 pages
            rows = verify_pdfjs_chunked(
                source=source,
                candidate=candidate,
                pages=4,
                threshold=2,
                chunk_size=2,
                workers=2,
            )
            self.assertEqual(len(rows), 4)
            self.assertEqual([r["page"] for r in rows], [1, 2, 3, 4])
            self.assertTrue(all(r["ok"] for r in rows))
            self.assertTrue(all(r["exact"] for r in rows))

    def test_direct_inject_toc(self):
        """Direct test of inject_toc onto a PDF document."""
        from pypdf import PdfWriter, PdfReader
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            src_pdf = tmp / "src.pdf"
            dst_pdf = tmp / "dst.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with src_pdf.open("wb") as f:
                writer.write(f)
            toc_tree = [{"title": "Bookmark", "page_idx": 0, "children": []}]
            inject_toc(src_pdf, dst_pdf, toc_tree)
            reader = PdfReader(str(dst_pdf))
            self.assertEqual(len(reader.outline), 1)

    def test_synthetic_valid_toc(self):
        """Test hierarchical TOC extraction, count, and outline injection into converted PDF."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "valid_toc.note"
            out_pdf = tmp / "out_toc.pdf"
            audit_dir = tmp / "audit"

            toc_items = [
                {"id": "c1", "title": "Chapter 1", "page_uuid": "p1", "page_idx": 0, "parent_id": None},
                {"id": "s1.1", "title": "Section 1.1", "page_uuid": "p1", "page_idx": 0, "parent_id": "c1"},
                {"id": "c2", "title": "Chapter 2", "page_uuid": "p2", "page_idx": 1, "parent_id": None},
                {"id": "s2.1", "title": "Section 2.1", "page_uuid": "p2", "page_idx": 1, "parent_id": "c2"},
            ]
            create_test_note(
                note_file,
                pages=["p1", "p2"],
                shapes=[
                    {"pen_type": 6, "uuid": "txt-1", "text_plain": "Page 1 text"},
                    {"pen_type": 6, "uuid": "txt-2", "text_plain": "Page 2 text"},
                ],
                toc_items=toc_items,
            )

            # 1. Preflight detects TOC items
            info = inspect_note(note_file)
            self.assertEqual(info["toc_items_count"], 4)

            # 2. extract_toc hierarchy
            tree = extract_toc(note_file)
            self.assertEqual(len(tree), 2)
            self.assertEqual(count_toc_nodes(tree), 4)

            self.assertEqual(tree[0]["title"], "Chapter 1")
            self.assertEqual(tree[0]["page_idx"], 0)
            self.assertEqual(len(tree[0]["children"]), 1)
            self.assertEqual(tree[0]["children"][0]["title"], "Section 1.1")
            self.assertEqual(tree[0]["children"][0]["page_idx"], 0)

            self.assertEqual(tree[1]["title"], "Chapter 2")
            self.assertEqual(tree[1]["page_idx"], 1)
            self.assertEqual(len(tree[1]["children"]), 1)
            self.assertEqual(tree[1]["children"][0]["title"], "Section 2.1")
            self.assertEqual(tree[1]["children"][0]["page_idx"], 1)

            # 3. convert_note_to_pdf injects outline
            report = convert_note_to_pdf(
                note_path=note_file,
                output_pdf=out_pdf,
                audit_dir=audit_dir,
                skip_optimize=True,
                strict=True,
            )
            self.assertEqual(report["status"], "success")
            self.assertIn("toc", report)
            self.assertTrue(report["toc"]["injected"])
            self.assertEqual(report["toc"]["item_count"], 4)
            self.assertEqual(report["toc"]["root_count"], 2)

            # 4. Verify PDF outline with pypdf
            from pypdf import PdfReader
            reader = PdfReader(str(out_pdf))
            outline = reader.outline
            self.assertIsNotNone(outline)
            self.assertGreaterEqual(len(outline), 2)
            root_titles = [item.title for item in outline if hasattr(item, "title")]
            self.assertEqual(root_titles, ["Chapter 1", "Chapter 2"])

    def test_synthetic_cyclic_toc(self):
        """Test that cycles in TOC parent-child hierarchy are detected, severed, and promoted without recursion error."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "cyclic_toc.note"

            cyclic_items = [
                {"id": "nodeA", "title": "Node A", "page_uuid": "p1", "page_idx": 0, "parent_id": "nodeB"},
                {"id": "nodeB", "title": "Node B", "page_uuid": "p1", "page_idx": 0, "parent_id": "nodeA"},
            ]
            create_test_note(note_file, pages=["p1"], toc_items=cyclic_items)

            tree = extract_toc(note_file)
            total = count_toc_nodes(tree)
            self.assertEqual(total, 2)
            all_titles = set()
            def collect_titles(nodes):
                for n in nodes:
                    all_titles.add(n["title"])
                    collect_titles(n.get("children", []))
            collect_titles(tree)
            self.assertIn("Node A", all_titles)
            self.assertIn("Node B", all_titles)

    def test_synthetic_single_page_toc(self):
        """Test single-page export TOC remapping: remaps target page to 0, drops others, promotes orphans."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "sp_toc.note"

            toc_items = [
                {"id": "c1", "title": "Chapter 1", "page_uuid": "p1", "page_idx": 0, "parent_id": None},
                {"id": "s1.1", "title": "Section 1.1", "page_uuid": "p2", "page_idx": 1, "parent_id": "c1"},
                {"id": "s1.1.1", "title": "Sub 1.1.1", "page_uuid": "p2", "page_idx": 1, "parent_id": "s1.1"},
                {"id": "c2", "title": "Chapter 2", "page_uuid": "p3", "page_idx": 2, "parent_id": None},
            ]
            create_test_note(note_file, pages=["p1", "p2", "p3"], toc_items=toc_items)

            # Export page 2 (1-based -> target index 1 / p2)
            tree_p2 = extract_toc(note_file, page=2)
            self.assertEqual(len(tree_p2), 1)  # s1.1 promoted to root
            root = tree_p2[0]
            self.assertEqual(root["title"], "Section 1.1")
            self.assertEqual(root["page_idx"], 0)
            self.assertEqual(len(root["children"]), 1)

            child = root["children"][0]
            self.assertEqual(child["title"], "Sub 1.1.1")
            self.assertEqual(child["page_idx"], 0)

            # Export page 1 (1-based -> target index 0 / p1)
            tree_p1 = extract_toc(note_file, page=1)
            self.assertEqual(len(tree_p1), 1)
            self.assertEqual(tree_p1[0]["title"], "Chapter 1")
            self.assertEqual(tree_p1[0]["page_idx"], 0)
            self.assertEqual(len(tree_p1[0]["children"]), 0)

    def test_synthetic_invalid_destination_toc(self):
        """Test that bookmarks with invalid/non-existent page_uuid are dropped and valid children promoted."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "inv_dest.note"

            toc_items = [
                {"id": "bad", "title": "Bad Dest", "page_uuid": "nonexistent_page", "page_idx": 99, "parent_id": None},
                {"id": "good_child", "title": "Good Child", "page_uuid": "p1", "page_idx": 0, "parent_id": "bad"},
            ]
            create_test_note(note_file, pages=["p1"], toc_items=toc_items)

            tree = extract_toc(note_file)
            self.assertEqual(len(tree), 1)
            self.assertEqual(tree[0]["title"], "Good Child")
            self.assertEqual(tree[0]["page_idx"], 0)
            self.assertEqual(len(tree[0]["children"]), 0)

    def test_no_toc_flag(self):
        """Test that --no-toc flag bypasses outline injection."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "no_toc.note"
            out_pdf = tmp / "out_no_toc.pdf"
            audit_dir = tmp / "audit"

            toc_items = [
                {"id": "c1", "title": "Chapter 1", "page_uuid": "p1", "page_idx": 0, "parent_id": None},
            ]
            create_test_note(
                note_file,
                pages=["p1"],
                shapes=[{"pen_type": 6, "uuid": "txt-1", "text_plain": "Text"}],
                toc_items=toc_items,
            )

            report = convert_note_to_pdf(
                note_path=note_file,
                output_pdf=out_pdf,
                audit_dir=audit_dir,
                no_toc=True,
                skip_optimize=True,
                strict=True,
            )
            self.assertEqual(report["status"], "success")
            self.assertNotIn("toc", report)

            from pypdf import PdfReader
            reader = PdfReader(str(out_pdf))
            self.assertEqual(len(reader.outline), 0)

    def test_stroke_width_cli_pass_through(self):
        """Test stroke width CLI options forwarding to renderer command."""
        with tempfile.TemporaryDirectory(prefix="tst-", dir=tempfile.gettempdir()) as tmpdir:
            tmp = Path(tmpdir)
            note_file = tmp / "stroke.note"
            create_test_note(note_file)
            audit_dir = tmp / "audit"

            with patch("subprocess.run") as mock_run:
                def fake_run(cmd, **kwargs):
                    out_arg = Path(cmd[2])
                    out_arg.write_bytes(b"%PDF-1.4 dummy")
                    res = MagicMock()
                    res.returncode = 0
                    res.stdout = ""
                    res.stderr = ""
                    return res

                mock_run.side_effect = fake_run

                report = convert_note_to_pdf(
                    note_path=note_file,
                    audit_dir=audit_dir,
                    stroke_mode="device-native",
                    fixed_stroke_width="0.35mm",
                    stroke_scale=0.24,
                    skip_optimize=True,
                    strict=True,
                )

                cmd = report["renderer"]["args"]
                self.assertIn("--stroke-mode", cmd)
                self.assertIn("device-native", cmd)
                self.assertIn("--fixed-stroke-width", cmd)
                self.assertIn("0.35mm", cmd)
                self.assertIn("--stroke-scale", cmd)
                self.assertIn("0.24", cmd)

    def test_me505_toc_if_present(self):
        """Integration test on real notebook ME 505.note if present on host."""
        selected = os.environ.get('PDF_NOTE_TEST_INPUT')
        if not selected:
            self.skipTest('Set PDF_NOTE_TEST_INPUT to opt in')
        self.assertTrue(Path(selected).is_file(), 'Invalid PDF_NOTE_TEST_INPUT')
        tree = extract_toc(selected)
        self.assertGreaterEqual(count_toc_nodes(tree), 0)




class RepairTests(unittest.TestCase):
    def test_failures_never_publish_and_opt_out_is_explicit(self):
        from unittest.mock import patch
        for failing_stage in ('extract_toc', 'inject_toc'):
            with self.subTest(stage=failing_stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                note = create_test_note(root/'n.note', toc_items=[{'id':'a', 'title':'A', 'page_uuid':'p1', 'page_idx':0}])
                with patch('note_to_pdf.' + failing_stage, side_effect=ValueError('forced failure')):
                    result = convert_note_to_pdf(note, root/'out.pdf', audit_dir=root/'audit', skip_optimize=True)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse((root/'out.pdf').exists())
                self.assertFalse(Path(result['renderer']['args'][2]).parent.exists())
                self.assertTrue((Path(result['audit_job_dir'])/'report.json').exists())
                with patch('note_to_pdf.extract_toc', side_effect=AssertionError('must bypass')):
                    result = convert_note_to_pdf(note, root/'bypass.pdf', audit_dir=root/'audit', skip_optimize=True, no_toc=True)
                self.assertEqual(result['status'], 'success')

    def test_normalized_ids_conflicts_and_depth(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            parent = '11111111-2222-3333-4444-555555555555'
            items = [{'id':parent, 'title':'A', 'page_uuid':'p1', 'page_idx':0},
                     {'id':'child', 'parent_id':'\0 ' + parent.replace('-', '').upper() + ' ', 'title':'B', 'page_uuid':'p1', 'page_idx':0}]
            note = create_test_note(root/'a.note', toc_items=items)
            self.assertEqual(len(extract_toc(note)[0]['children']), 1)
            items.append(dict(items[0], id=parent.replace('-', ''), title='Conflict'))
            create_test_note(note, toc_items=items)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                extract_toc(note)
            for depth in (64, 65, 1100):
                items = [{'id':str(i), 'parent_id':str(i-1) if i else None, 'title':str(i), 'page_uuid':'p1', 'page_idx':0} for i in range(depth)]
                create_test_note(note, toc_items=items)
                if depth == 64:
                    self.assertEqual(count_toc_nodes(extract_toc(note)), 64)
                else:
                    with self.assertRaisesRegex(ValueError, '64 levels'):
                        extract_toc(note)

    def test_multi_note_order_and_rendered_destinations(self):
        import zipfile
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            combined = root/'multi.note'
            with zipfile.ZipFile(combined, 'w') as out:
                for prefix, title in [('z/', 'Z'), ('a/', 'A')]:
                    n = create_test_note(root/(title+'.note'), pages=['p1', 'blank'], shapes=[{'pen_type':6, 'text_plain':title}],
                                         toc_items=[{'id':'same-id', 'title':title, 'page_uuid':'p1', 'page_idx':0}])
                    with zipfile.ZipFile(n) as src:
                        for name in src.namelist(): out.writestr(prefix+name, src.read(name))
            info = inspect_note(combined)
            self.assertEqual([n['prefix'] for n in info['notes']], ['a/', 'z/'])
            result = convert_note_to_pdf(combined, root/'out.pdf', audit_dir=root/'audit', skip_optimize=True)
            self.assertEqual(result['status'], 'success')
            pdf = PdfReader(root/'out.pdf')
            self.assertEqual([pdf.get_destination_page_number(x) for x in pdf.outline], [0, 2])
            self.assertEqual([p.extract_text().strip() for p in pdf.pages], ['A', '', 'Z', ''])
            result = convert_note_to_pdf(combined, root/'one.pdf', audit_dir=root/'audit', skip_optimize=True, page=3)
            pdf = PdfReader(root/'one.pdf')
            self.assertEqual(pdf.outline[0].title, 'Z')
            self.assertEqual(pdf.get_destination_page_number(pdf.outline[0]), 0)

    def test_malformed_toc_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            note = create_test_note(root/'bad.note', extra_files={'toc/pb/bad':b'\x0a\xff'})
            result = convert_note_to_pdf(note, root/'out.pdf', audit_dir=root/'audit', skip_optimize=True)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse((root/'out.pdf').exists())


if __name__ == "__main__":
    unittest.main()
