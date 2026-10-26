"""Dependency-bound immutable cache entries for completed evidence operations."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil


SCHEMA = 1
REQUIRED_CONTEXT = {'operation', 'source_sha256', 'candidate_sha256', 'page', 'crop',
                    'renderer', 'renderer_version', 'geometry', 'resolution',
                    'background', 'thresholds', 'metric_version'}


def canonical(context):
    missing = REQUIRED_CONTEXT-set(context)
    if missing:
        raise ValueError('Cache context missing: ' + ', '.join(sorted(missing)))
    return json.dumps(context, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class EvidenceCache:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError('Cache root must not be a shortcut or symbolic link.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.stats = Counter()

    def key(self, context):
        # Windows release paths need room for payload names; markers still verify the full context.
        return hashlib.sha256(canonical(context)).hexdigest()[:32]

    def _read(self, context, record=True):
        entry = self.root/self.key(context)
        marker = entry/'complete.json'
        if not marker.is_file():
            if record:self.stats['misses'] += 1
            return None
        try:
            complete = json.loads(marker.read_text(encoding='utf-8'))
            payload = entry/complete['payload']
            if (payload.parent != entry or complete['schema'] != SCHEMA or complete['context'] != context or
                    not payload.is_file() or payload.stat().st_size != complete['bytes'] or
                    hashlib.sha256(payload.read_bytes()).hexdigest() != complete['payload_sha256']):
                raise ValueError('Invalid completion marker')
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            if record:
                self.stats['misses'] += 1
                self.stats['invalid'] += 1
            return None
        if record:self.stats['hits'] += 1
        return payload

    def load_json(self, context):
        payload = self._read(context)
        if payload is None:
            return None
        try:
            return json.loads(payload.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            self.stats['invalid'] += 1
            return None

    def materialize(self, context, destination):
        payload = self._read(context)
        if payload is None:
            return False
        with payload.open('rb') as source, Path(destination).open('xb') as target:
            shutil.copyfileobj(source, target)
        return True

    def _store(self, context, data, suffix):
        existing = self._read(context, False)
        if existing is not None:
            return existing
        entry = self.root/self.key(context)
        entry.mkdir(parents=True, exist_ok=True)
        payload = entry/f'payload{suffix}'
        temporary = entry/f'.payload.{os.getpid()}.tmp'
        temporary.write_bytes(data)
        os.replace(temporary, payload)
        complete = {'schema': SCHEMA, 'context': context, 'payload': payload.name,
                    'bytes': len(data), 'payload_sha256': hashlib.sha256(data).hexdigest()}
        marker = entry/'complete.json'
        marker_tmp = entry/f'.complete.{os.getpid()}.tmp'
        marker_tmp.write_text(json.dumps(complete, sort_keys=True), encoding='utf-8')
        os.replace(marker_tmp, marker)
        self.stats['writes'] += 1
        return payload

    def store_json(self, context, value):
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return self._store(context, data, '.json')

    def store_file(self, context, source):
        return self._store(context, Path(source).read_bytes(), Path(source).suffix or '.bin')
