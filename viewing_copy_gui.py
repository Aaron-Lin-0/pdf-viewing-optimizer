"""Accessible Windows picker for sequential verified viewing-copy jobs."""

import multiprocessing
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

from batch_workflow import (BatchController, NoteOptions, NOTE_WIDTH_LABELS, OPTIMIZER_LABELS, PAPER_LABELS, PROFILE_LABELS, confirmation_text, dependency_diagnostics,
                            dependency_report, plan_batch, resource_summary, system_resources)

STAGE_PROGRESS = {
    'quick input checks': (2, 5),
    'qpdf preflight': (5, 10),
    'renderer and policy context': (10, 18),
    'verify current-revision snapshot': (18, 21),
    'exact inlining, deduplication and cleanup': (21, 30),
    'pack transformed copy': (30, 36),
    'verify lossless packing': (36, 41),
    'quick compression': (18, 41),
    'qpdf validity check': (41, 45),
    'lossless graph verification': (45, 95),
    'verify preserved content on every page': (45, 67),
    'PDF.js appearance on every page': (67, 86),
    'high-resolution and print verification': (86, 97),
    'renderer preflight': (2, 8),
    'parallel candidate generation': (8, 15),
    'source browser viability': (58, 68),
    'balanced performance timing': (68, 80),
    'study input checks': (2, 10),
    'study current-revision snapshot': (10, 18),
    'study rasterization': (18, 70),
    'study packing': (70, 75),
    'study saved verification': (75, 95),
    'publication': (98, 99),
}
VERIFIED_PAGES = re.compile(
    r'(?:Verified vector content/navigation/appearance|PDF\.js appearance verified|Study pages rasterized|Study pages verified): (\d+)/(\d+)')


def stage_progress(stage, previous, input_kind='pdf'):
    if input_kind == 'note':
        stages={'note inspection':(0,10),'note conversion':(10,30),'note bookmarks':(30,35),
                'note optimization':(35,40),'note publication':(98,99)}
        if stage in stages:return stages[stage]
        low,high=stage_progress(stage,0)
        return 40+low*.55,40+high*.55
    if stage.startswith(('candidate generation: ', 'preservation verification: ')):
        # Thorough can try an unknown number of candidates before timing.
        start=max(previous,8)
        return start+max(0,58-start)*.08, start+max(0,58-start)*.2
    if stage.startswith('final acceptance: '):
        start=max(previous,80)
        return start+max(0,97-start)*.08, start+max(0,97-start)*.6
    return STAGE_PROGRESS.get(stage,(min(98,previous+2),min(99,previous+4)))


def verified_page_fraction(line):
    match=VERIFIED_PAGES.fullmatch(line)
    if match:
        done,total=map(int,match.groups())
        if 0<done<=total:return done/total


def progress_text(completed, total):
    return 'No batch running' if not total else f'{completed} of {total} files complete'


def inspect_in_worker(source, messages):
    with messages:
        try:
            from experiment_manifest import analyze_drawing_opportunity
            report=analyze_drawing_opportunity(source,progress=lambda done,total:
                messages.send(('inspection progress',(done,total))))
            summary={key:report[key] for key in ('source_sha256','inventory','first_supported_subset','denominator_tracking')}
            inventory=summary['inventory']
            widths=inventory.pop('local_widths')
            # Long wrapped lines can stall the activity log after the scan finishes.
            preview=', '.join(str(width) for width in widths[:8])
            if len(widths)>8 or len(preview)>160:
                preview=f'{preview[:160]}… ({len(widths):,} distinct widths)'
            inventory['line_width_summary']=preview or 'none'
            messages.send(('inspection complete',summary))
        except Exception as exc:
            messages.send(('inspection failed',str(exc)))


def start_inspection(source):
    context=multiprocessing.get_context('spawn')
    receive,send=context.Pipe(duplex=False)
    process=context.Process(target=inspect_in_worker,args=(source,send),daemon=True)
    try:
        process.start()
    except BaseException:
        receive.close()
        raise
    finally:
        send.close()
    return process,receive


def collect_pdf_paths(pick,add_more):
    selected=[]
    while True:
        batch=pick()
        if not batch:return tuple(selected)
        selected.extend(batch)
        if not add_more(len(selected)):return tuple(selected)


def append_activity(log, text, heading=False):
    follow_tail=log.yview()[1]>=1.0 and not log.tag_ranges('sel')
    log.configure(state='normal')
    log.insert('end',text+'\n',('heading',) if heading else ())
    log.configure(state='disabled')
    if follow_tail:log.see('end')


def review_jobs(parent, text, *, can_start=True):
    return batch_dialog(parent, text, can_start=can_start)


def batch_dialog(parent, text, *, jobs=None, can_start=True):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import messagebox, ttk

    reviewing=jobs is None
    heading='Review batch' if reviewing else 'Batch complete'
    dialog=tk.Toplevel(parent);dialog.withdraw();dialog.title(heading);dialog.minsize(620,360)
    dialog.transient(parent);dialog.columnconfigure(0,weight=1);dialog.rowconfigure(1,weight=1)
    ttk.Label(dialog,text=heading,font='TkHeadingFont').grid(row=0,column=0,sticky='w',padx=16,pady=(16,8))
    body=ttk.Frame(dialog);body.grid(row=1,column=0,sticky='nsew',padx=16);body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
    paths=tk.Text(body,wrap='word',height=18,takefocus=True,font='TkDefaultFont',padx=10,pady=8,spacing3=2)
    scroll=ttk.Scrollbar(body,orient='vertical',command=paths.yview)
    paths.configure(yscrollcommand=scroll.set);paths.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
    bold=tkfont.nametofont('TkDefaultFont').copy();bold.configure(weight='bold')
    paths.tag_configure('heading',font=bold)
    lines=text.splitlines()
    for index,line in enumerate(lines):
        file_heading=index+1<len(lines) and lines[index+1].startswith('  Source: ')
        tag=('heading',) if index==0 or file_heading or line in {
            'Run settings','Files and outputs','Verification resources'} else ()
        paths.insert('end',line+'\n',tag)
    if jobs is not None:
        def open_location(target, is_report=False):
            try:
                if is_report:
                    notepad = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'notepad.exe'
                    subprocess.Popen([str(notepad), str(target)], cwd=target.parent)
                else:
                    os.startfile(str(target.parent))
            except OSError as exc:
                messagebox.showerror('Could not open location', 'Use Copy path to find it manually.\n\n' + str(exc),
                                     parent=dialog)

        def copy_path(target, button):
            dialog.clipboard_clear()
            dialog.clipboard_append(str(target))
            button.configure(text='Copied')

        for job in jobs:
            paths.insert('end', '\n' + job.source.name + '\n', ('heading',))
            is_report = job.status == 'no improvement'
            if job.status in {'optimized', 'converted'}:
                outcome = 'Viewing copy saved.' if job.status == 'optimized' else (job.error or 'BOOX note converted.')
                if job.profile == 'bitmap' and job.input_kind == 'pdf':
                    outcome += ' Compare the copy with the original before studying.'
                target = job.output
            elif is_report:
                outcome = 'Keep using the original. ' + (job.error or 'No copy met the selected mode’s checks.')
                target = job.output.with_suffix('.md')
            else:
                outcome = f'{job.status.capitalize()}. Keep using the original.'
                if job.error:
                    outcome += '\n' + job.error
                target = None
            paths.insert('end', outcome + '\n')
            if target is not None:
                paths.insert('end', str(target) + '\n')
                actions = ttk.Frame(paths)
                if os.name == 'nt':
                    ttk.Button(actions, text='Open report' if is_report else 'Open folder',
                               command=lambda target=target, is_report=is_report: open_location(target, is_report)).pack(side='left')
                copy_button = ttk.Button(actions, text='Copy path')
                copy_button.configure(command=lambda target=target, button=copy_button: copy_path(target, button))
                copy_button.pack(side='left', padx=(6, 0))
                paths.window_create('end', window=actions)
                paths.insert('end', '\n')
    paths.configure(state='disabled')
    controls=ttk.Frame(dialog);controls.grid(row=2,column=0,sticky='e',padx=16,pady=16)
    accepted={'value':False}
    def finish(value):accepted['value']=value;dialog.destroy()
    close_button = ttk.Button(controls, text='Back to settings' if reviewing else 'Close',
                              command=lambda:finish(False))
    close_button.grid(row=0, column=0)
    primary = close_button
    if reviewing and can_start:
        primary = ttk.Button(controls, text='Start batch', underline=0, command=lambda:finish(True))
        primary.grid(row=0, column=1, padx=(8,0))
        dialog.bind('<Alt-s>',lambda _event:finish(True))
    dialog.bind('<Escape>',lambda _event:finish(False))
    dialog.protocol('WM_DELETE_WINDOW',lambda:finish(False))
    dialog.geometry(f'+{parent.winfo_x()+28}+{parent.winfo_y()+28}')
    dialog.deiconify();dialog.grab_set();primary.focus_set();parent.wait_window(dialog)
    return accepted['value']


def main():
    import gc
    gc.collect()
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk

    root=tk.Tk();root.title('PDF Viewing Optimizer — checking setup');root.geometry('900x780');root.minsize(760,650)
    events=queue.Queue();controller=None;running=False;active_source=None;active_stage=None;setup_ready=False
    closed=False;task_token=0;poll_after=None
    setup_state = 'Checking setup…'
    profile_keys = {label: key for key, label in PROFILE_LABELS.items()}
    paper_keys = {label: key for key, label in PAPER_LABELS.items()}
    batch_total=0;batch_done=0;batch_started=None;inspection_started=None;inspection_process=None
    file_progress=stage_start=stage_end=0
    selected_paths=()
    inspected_pdf=None
    heading=tkfont.nametofont('TkHeadingFont').copy();heading.configure(size=16,weight='bold')
    bold=tkfont.nametofont('TkDefaultFont').copy();bold.configure(weight='bold')
    frame=ttk.Frame(root,padding=16);frame.grid(sticky='nsew');root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(5,weight=1)
    ttk.Label(frame,text='PDF viewing copies',font=heading).grid(row=0,column=0,sticky='w')
    selection_text=tk.StringVar(value='Choose files, then select Review and run to confirm where your copies will be saved. Originals stay intact.')
    selection_label=ttk.Label(frame,textvariable=selection_text,wraplength=760)
    selection_label.grid(row=1,column=0,sticky='ew',pady=(3,12))
    resources=system_resources();worker_count=tk.IntVar(value=min(2,resources['logical_cpu_count']))
    parallel_candidates=tk.BooleanVar(value=False)
    profile_choice=tk.StringVar(value=PROFILE_LABELS['quick'])
    optimizer_choice=tk.StringVar(value=OPTIMIZER_LABELS['adaptive'])
    resource_text=tk.StringVar()
    resource_label=ttk.Label(frame,textvariable=resource_text,wraplength=760)
    resource_label.grid(row=2,column=0,sticky='ew',pady=(0,8))
    current=tk.StringVar(value='Checking setup…');current_label=ttk.Label(frame,textvariable=current,wraplength=760,font=bold)
    current_label.grid(row=3,column=0,sticky='ew',pady=(0,6))
    progress_row=ttk.Frame(frame);progress_row.grid(row=4,column=0,sticky='ew',pady=(0,10));progress_row.columnconfigure(0,weight=1)
    progress_status=tk.StringVar(value=progress_text(0,0));ttk.Label(progress_row,textvariable=progress_status).grid(row=0,column=0,sticky='w',pady=(0,3))
    elapsed_status=tk.StringVar(value='Total elapsed: 0:00:00');ttk.Label(progress_row,textvariable=elapsed_status).grid(row=0,column=1,sticky='e',pady=(0,3))
    progress=ttk.Progressbar(progress_row,mode='determinate',maximum=1,value=0)
    progress.grid(row=1,column=0,columnspan=2,sticky='ew')
    log_frame=ttk.LabelFrame(frame,text='Activity log',padding=6);log_frame.grid(row=5,column=0,sticky='nsew')
    log_frame.columnconfigure(0,weight=1);log_frame.rowconfigure(0,weight=1)
    log=tk.Text(log_frame,wrap='word',state='disabled',takefocus=True,font='TkDefaultFont',padx=8,pady=6,spacing3=2)
    log.tag_configure('heading',font=bold)
    scroll=ttk.Scrollbar(log_frame,orient='vertical',command=log.yview)
    log.configure(yscrollcommand=scroll.set);log.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
    controls=ttk.Frame(frame);controls.grid(row=6,column=0,sticky='ew',pady=(12,0));controls.columnconfigure(2,weight=1)
    actions=ttk.Frame(controls);actions.grid(row=6,column=0,columnspan=6,sticky='e',pady=(8,0))
    worker_selector=ttk.Spinbox(controls,from_=1,to=resources['logical_cpu_count'],width=5,
                                textvariable=worker_count,wrap=False)
    def update_resources(*_args):
        profile = profile_keys[profile_choice.get()]
        if profile == 'quick':
            resource_text.set('Makes a smaller copy when preservation checks pass. Text stays searchable and handwriting stays sharp.')
        elif profile == 'bitmap':
            resource_text.set('Creates a study copy from supported handwriting or scanned pages. Pages become images, so zoom and print detail are limited. The copy can be larger.')
        else:
            try:
                workers = worker_count.get()
            except tk.TclError:
                return
            resource_text.set('Tests which copies draw faster. Takes longer and requires Firefox or Zen.\n'
                              + resource_summary(workers, resources, compact=True))
    worker_count.trace_add('write',update_resources)

    def set_status(text):
        current.set(text);root.title('PDF Viewing Optimizer — '+text[:80])

    def update_progress():
        value=batch_done+file_progress/100
        progress.configure(value=value)
        label=progress_text(batch_done,batch_total)
        if batch_total:
            percent=int(100*value/batch_total)
            label+=f'  ·  {percent}%'+(' (approx.)' if batch_done<batch_total else '')
        progress_status.set(label)

    def write(text, heading=False):
        append_activity(log,text,heading)

    def enable_inputs(enabled):
        choose_button.configure(state='normal' if enabled else 'disabled')
        inspect_button.configure(state='normal' if enabled else 'disabled')
        review_button.configure(state='normal' if enabled and selected_paths else 'disabled')
        review_inspected_button.configure(state='normal' if enabled and inspected_pdf else 'disabled')
        if inspected_pdf:review_inspected_button.grid()
        else:review_inspected_button.grid_remove()
        study_paper_selector.configure(state='readonly' if enabled else 'disabled')
        study_dpi_selector.configure(state='readonly' if enabled else 'disabled')
        study_text_selector.configure(state='normal' if enabled else 'disabled')
        for widget in note_widgets:
            widget.configure(state='normal' if enabled else 'disabled')
        if enabled:update_note_controls()

    def check_setup(show_dialog=True):
        nonlocal setup_ready,task_token
        if closed or running or setup_button.instate(['disabled']):return
        task_token+=1;token=task_token
        setup_ready=False
        profile = profile_keys[profile_choice.get()]
        set_status('Checking setup…');setup_button.configure(state='disabled');enable_inputs(False)
        profile_selector.configure(state='disabled');optimizer_selector.configure(state='disabled')
        worker_selector.configure(state='disabled');parallel_selector.configure(state='disabled')
        def work():
            try:events.put(('setup complete',(token,show_dialog,dependency_diagnostics(profile))))
            except Exception as exc:events.put(('setup failed',(token,show_dialog,exc)))
        threading.Thread(target=work,daemon=True).start()

    def inspect_pdf():
        nonlocal task_token,inspection_started,inspection_process,inspected_pdf
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before inspecting PDFs.',parent=root)
            return
        selected=filedialog.askopenfilename(parent=root,title="Choose PDF to inspect drawing opportunities",
                                            filetypes=(("PDF files","*.pdf"),))
        if not selected:return
        inspected_pdf=None
        task_token+=1;token=task_token
        set_status('Inspecting drawing opportunities…')
        inspection_started=time.monotonic()
        elapsed_status.set('Inspection elapsed: 00:00:00')
        progress_status.set('Overall inspection · Opening and checking PDF…')
        progress.configure(mode='indeterminate',maximum=100,value=0)
        progress.start(80)
        write(f"\nInspecting drawing opportunities: {selected}…")
        enable_inputs(False);setup_button.configure(state='disabled')
        profile_selector.configure(state='disabled')
        optimizer_selector.configure(state='disabled');worker_selector.configure(state='disabled')
        parallel_selector.configure(state='disabled')
        def work(process,messages):
            with messages:
                try:
                    while True:
                        kind,value=messages.recv()
                        if kind!='inspection progress':break
                        events.put((kind,(token,*value)))
                except (EOFError,OSError):
                    kind,value='inspection failed','The inspection worker exited before returning a report.'
            process.join()
            events.put((kind,(token,selected,value)))
        try:
            inspection_process,messages=start_inspection(selected)
        except OSError as exc:
            events.put(('inspection failed',(token,selected,exc)))
            return
        threading.Thread(target=work,args=(inspection_process,messages),daemon=True).start()

    def finish_inspection(success):
        nonlocal inspection_started,inspection_process
        elapsed=time.monotonic()-inspection_started
        elapsed_status.set(f'Inspection elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
        inspection_started=None;inspection_process=None
        progress.stop()
        progress.configure(mode='determinate',value=100 if success else 0)
        progress_status.set('Overall inspection · 100% complete' if success else 'Inspection failed')
        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
        enable_inputs(setup_ready)

    def choose():
        nonlocal selected_paths
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before selecting PDFs.',parent=root)
            return
        selected=collect_pdf_paths(
            lambda:filedialog.askopenfilenames(parent=root,title="Choose PDFs or BOOX notes to add",filetypes=(("PDF and BOOX files","*.pdf *.note"),("PDF files","*.pdf"),("BOOX notes","*.note"))),
            lambda count:messagebox.askyesno('Add files from another folder?',
                f'{count} {"file" if count==1 else "files"} selected. Add files from another folder?',parent=root))
        if not selected:
            return
        selected_paths=tuple(dict.fromkeys(selected))
        update_note_controls()
        count=len(selected_paths)
        selection_text.set(f'{count} {"file" if count==1 else "files"} selected. Select Review and run to confirm settings and destinations.')
        write('\nSelected files',heading=True)
        write('\n'.join(selected_paths))
        enable_inputs(True)
        review_button.focus_set()

    def plan_selected(inspection=None):
        nonlocal task_token
        selected=selected_paths
        inspected_hash=inspection_summary=None
        if inspection:
            path,inspected_hash,inspection_summary=inspection
            selected=(path,)
        if not selected or not setup_ready:
            return
        profile=profile_keys[profile_choice.get()]
        optimizer='adaptive' if profile in ('quick','bitmap') else {label:key for key,label in OPTIMIZER_LABELS.items()}[optimizer_choice.get()]
        workers=None
        if profile=='thorough' and any(Path(p).suffix.lower()!='.note' for p in selected):
            try:workers=worker_count.get()
            except tk.TclError:
                messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.',parent=root)
                return
            if not 1<=workers<=resources['logical_cpu_count']:
                messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.',parent=root)
                return
        parallel=profile=='thorough' and optimizer=='adaptive' and parallel_candidates.get()
        try:
            options = NoteOptions({label:key for key,label in NOTE_WIDTH_LABELS.items()}[note_mode.get()], note_width.get()+note_unit.get(), float(note_scale.get()) if {label:key for key,label in NOTE_WIDTH_LABELS.items()}[note_mode.get()]=='scale' else 1.0, note_bookmarks.get(), note_optimize.get()) if any(Path(p).suffix.lower()=='.note' for p in selected) else NoteOptions()
            options.validate()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror('BOOX settings',str(exc),parent=root)
            return
        paper = paper_keys[study_paper.get()]
        dpi = int(study_dpi.get())
        flatten_text = profile=='bitmap' and study_flatten_text.get()
        converter = note_renderer.get() or None
        task_token+=1;token=task_token
        set_status('Inspecting selected files…');enable_inputs(False);setup_button.configure(state='disabled')
        profile_selector.configure(state='disabled');optimizer_selector.configure(state='disabled')
        worker_selector.configure(state='disabled');parallel_selector.configure(state='disabled')
        def work():
            try:
                jobs=plan_batch(selected,optimizer,profile,
                                lambda path:events.put(('planning progress',(token,str(path)))),
                                note_options=options,renderer=converter,study_paper=paper,study_dpi=dpi,
                                study_flatten_text=flatten_text)
                if inspection and jobs[0].source_sha256 and jobs[0].source_sha256!=inspected_hash:
                    raise ValueError('The PDF changed since inspection. Inspect it again to use an updated report.')
                if profile=='thorough' and any(j.input_kind=='pdf' for j in jobs):
                    blockers=[r for r in dependency_diagnostics(profile) if not r['ok'] and not r['optional']]
                    if blockers:
                        from dataclasses import replace
                        jobs=tuple(replace(j,status='rejected',error=dependency_report(blockers)) if j.input_kind=='pdf' else j for j in jobs)
                events.put(('planning complete',(token,jobs,workers,parallel,inspection_summary)))
            except Exception as exc:events.put(('planning failed',(token,exc)))
        threading.Thread(target=work,daemon=True).start()

    def show_results(jobs):
        labels = {"optimized": "Copies saved", "converted": "Notes converted", "no improvement": "Reports only",
                  "failed": "Failed", "timeout": "Timed out", "budget exhausted": "Over budget",
                  "rejected": "Rejected", "skipped": "Skipped"}
        counts = [f"{label}: {count}" for status, label in labels.items()
                  if (count := sum(job.status == status for job in jobs))]
        summary = "\n".join(counts)
        write("\nBatch complete", heading=True)
        write(summary)
        batch_dialog(root, summary, jobs=jobs)

    def review_planned(jobs,workers,parallel,inspection_summary):
        nonlocal controller,running,active_source,active_stage,batch_total,batch_done,batch_started
        nonlocal selected_paths,file_progress
        ready = any(job.status == 'ready' for job in jobs)
        finished = not ready and any(job.status in {'optimized', 'converted', 'no improvement', 'failed'} for job in jobs)
        if finished:
            show_results(jobs)
            accepted = False
        else:
            text=confirmation_text(jobs,workers,resources,parallel)
            if inspection_summary and jobs[0].source_sha256:
                text=f'Drawing inspection\n{inspection_summary}\n\n{text}'
            accepted=review_jobs(root,text,can_start=ready)
        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
        enable_inputs(setup_ready)
        if not accepted or not ready:
            write("Selection kept. Adjust settings and review again when ready.")
            set_status(setup_state)
            (review_inspected_button if inspection_summary else review_button).focus_set()
            return
        controller = BatchController(jobs, lambda *event: events.put(event),verification_workers=workers,
                                     parallel_candidates=parallel)
        if not inspection_summary:
            selected_paths=()
            selection_text.set('Choose files, then select Review and run to confirm where your copies will be saved. Originals stay intact.')
        running=True;active_source=None;active_stage=None;batch_done=0;batch_started=time.monotonic();elapsed_status.set('Total elapsed: 0:00:00')
        batch_total=sum(job.status=='ready' for job in jobs)
        file_progress=0
        progress.configure(maximum=batch_total);update_progress()
        enable_inputs(False);setup_button.configure(state='disabled');stop_button.configure(state='normal')
        worker_selector.configure(state='disabled')
        optimizer_selector.configure(state='disabled')
        profile_selector.configure(state='disabled')
        parallel_selector.configure(state='disabled')
        write(f"Reviewed {len(jobs)} unique file(s).")

        def work():
            results = controller.run()
            events.put(("complete", results))

        threading.Thread(target=work, daemon=True).start()

    def stop():
        if controller and running:
            controller.request_stop()
            stop_button.configure(state="disabled")
            write("Stop requested; the current file will finish and queued files will be skipped.")

    def poll():
        nonlocal running,active_source,active_stage,batch_done,batch_started,setup_ready,setup_state,poll_after
        nonlocal file_progress,stage_start,stage_end,inspected_pdf
        try:
            # Let Windows process input and repaint between bursts of worker output.
            deadline=time.monotonic()+.025
            while time.monotonic()<deadline:
                kind, value = events.get_nowait()
                try:
                    if kind == 'setup complete':
                        token,show_dialog,rows=value
                        if token!=task_token or closed:continue
                        report=dependency_report(rows);write('\nSetup check',heading=True);write(report)
                        blockers=[row for row in rows if not row['ok'] and not row['optional']]
                        setup_ready = all(row.get('thorough_only') for row in blockers)
                        if not setup_ready:
                            setup_state = 'Setup needs attention'
                        elif blockers:
                            setup_state = 'Thorough needs setup. Switch to Direct or check setup for details.'
                        else:
                            setup_state = 'Ready'
                        setup_button.configure(state='normal')
                        profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready and not running)
                        set_status(setup_state)
                        if show_dialog:(messagebox.showerror if blockers else messagebox.showinfo)('Setup check',report,parent=root)
                    elif kind == 'setup failed':
                        token,show_dialog,error=value
                        if token!=task_token or closed:continue
                        setup_ready=False;setup_button.configure(state='normal');profile_selector.configure(state='readonly')
                        enable_inputs(False)
                        setup_state = 'Setup needs attention'
                        update_optimizer_controls();set_status(setup_state)
                        write('Setup check failed: '+str(error))
                        if show_dialog:messagebox.showerror('Setup check',str(error),parent=root)
                    elif kind == 'planning progress':
                        token,path=value
                        if token==task_token and not closed:set_status('Checking '+Path(path).name)
                    elif kind == 'planning complete':
                        token,jobs,workers,parallel,inspection_summary=value
                        if token!=task_token or closed:continue
                        review_planned(jobs,workers,parallel,inspection_summary)
                    elif kind == 'planning failed':
                        token,error=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready)
                        set_status('Selection failed');write('Selection failed: '+str(error));messagebox.showerror('Selection failed',str(error),parent=root)
                    elif kind == 'inspection progress':
                        token,done,total=value
                        if token!=task_token or closed:continue
                        if progress['mode']=='indeterminate':progress.stop()
                        # Leave room for the completed input checks and the pending report.
                        percent=10+85*done//total
                        progress.configure(mode='determinate',value=percent)
                        progress_status.set(f'Overall inspection · {percent}% (approx.)')
                        if done==total:set_status('Finishing drawing inspection…')
                        else:set_status(f'Inspecting page {done+1:,} of {total:,}…')
                    elif kind == 'inspection complete':
                        token,path,report=value
                        if token!=task_token or closed:continue
                        inv=report['inventory'];sub=report['first_supported_subset'];denom=report['denominator_tracking']
                        lines=[
                            f"\nDrawing Opportunity Inspection: {path}",
                            f"Status: {sub['status'].upper()}",
                            f"Explanation: {sub['explanation']}",
                            "Geometry inventory:",
                            f"  Multi-vertex stroked paths: {inv['multi_vertex_stroked_paths']:,}",
                            f"  Two-point stroke segments: {inv['two_point_segments']:,}",
                            f"  Cubic curves: {inv['curves']:,}",
                            f"  Variable-width transitions: {inv['variable_width_runs']:,}",
                            f"  Filled outlines: {inv['filled_outlines']:,}",
                            f"  Image stamps / annotations: {inv['image_stamps']:,} / {inv['annotations']:,}",
                            f"  Raster calls / images: {inv['raster_content']['image_calls']:,} / {inv['raster_content']['unique_images']:,}",
                            f"  Declared line widths: {inv['line_width_summary']}",
                            f"  Decoded path points / paints: {inv['point_counts']:,} points, {inv['paint_calls']:,} paints",
                        ]
                        skips=[f"  {k.replace('_', ' ').capitalize()}: {v}" for k,v in sub['skip_reasons'].items() if v>0]
                        if skips:
                            lines.append("Skip reasons & constraints:")
                            lines.extend(skips)
                        lines.append(f"Pages: {denom['total_pages']} total ({denom['pages_with_supported_opportunity']} supported, "
                                     f"{denom['pages_raster_only']} raster-only, {denom['pages_already_efficient']} already-efficient, "
                                     f"{denom['pages_unsupported_or_no_op']} unsupported/no-op).")
                        write("\n".join(lines))
                        total=denom['total_pages']
                        summary=(f"{total:,} {'page' if total==1 else 'pages'} inspected, "
                                 f"{denom['pages_with_supported_opportunity']:,} with drawing opportunities.")
                        inspected_pdf=(path,report['source_sha256'],summary)
                        finish_inspection(True)
                        set_status(setup_state)
                        review_inspected_button.focus_set()
                    elif kind == 'inspection failed':
                        token,path,error=value
                        if token!=task_token or closed:continue
                        finish_inspection(False)
                        set_status('Inspection failed')
                        write(f"\nInspection failed for {path}: {error}")
                        messagebox.showerror('Inspection failed',str(error),parent=root)
                    elif kind == "current":
                        active_source = value.source
                        active_stage = None
                        file_progress=stage_start=stage_end=0
                        set_status(f"Starting {active_source.name}")
                        write("\n" + active_source.name,heading=True)
                        write(f"Source: {active_source}")
                    elif kind == "output":
                        write(value)
                        fraction=verified_page_fraction(value)
                        if active_stage and fraction is not None:
                            file_progress=max(file_progress,stage_start+(stage_end-stage_start)*fraction)
                            update_progress()
                    elif kind == "stage":
                        active_stage = value
                        stage_start,stage_end=stage_progress(value[0],file_progress,next((j.input_kind for j in controller.jobs if j.source==active_source),'pdf'))
                        file_progress=max(file_progress,stage_start)
                        update_progress()
                    elif kind == "result":
                        active_stage=None
                        file_progress=0
                        batch_done=min(batch_total,batch_done+1);update_progress()
                        if value.status == "optimized":
                            write(f"Saved copy: {value.output}")
                        elif value.status == "converted":
                            write(f"{value.error}: {value.output}")
                        elif value.status == "no improvement":
                            write(f"No viewing copy created. Report: {value.output.with_suffix('.md')}")
                        elif value.status in {"rejected", "failed", "timeout", "budget exhausted"}:
                            write(f"{value.status.title()}: {value.source} — {value.error}")
                        elif value.status == "skipped":
                            write(f"Skipped: {value.source} — {value.error}")
                    elif kind == "complete":
                        running = False
                        elapsed=time.monotonic()-batch_started if batch_started is not None else 0
                        elapsed_status.set(f'Total elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
                        active_stage = None
                        batch_done=batch_total;update_progress()
                        enable_inputs(setup_ready)
                        setup_button.configure(state="normal")
                        stop_button.configure(state="disabled")
                        worker_selector.configure(state='normal')
                        optimizer_selector.configure(state='readonly')
                        profile_selector.configure(state='readonly')
                        update_optimizer_controls()
                        set_status("Complete")
                        show_results(value)
                        choose_button.focus_set()
                except Exception as exc:
                    if kind == 'inspection complete' and inspection_started is not None:
                        finish_inspection(False)
                        set_status('Inspection failed')
                    write(f"UI event handling error ({kind}): {exc}")
        except queue.Empty:
            pass
        finally:
            if running:
                elapsed=time.monotonic()-batch_started if batch_started is not None else 0
                elapsed_status.set(f'Total elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
                if active_stage and active_source:
                    stage,started=active_stage
                    set_status(f"{active_source.name} — {stage[:1].upper()+stage[1:]} ({time.monotonic()-started:.0f}s)")
            elif inspection_started is not None:
                elapsed=time.monotonic()-inspection_started
                elapsed_status.set(f'Inspection elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
            if not closed:poll_after=root.after(1 if not events.empty() else 100, poll)

    def close():
        nonlocal closed,task_token,poll_after
        if controller and not controller.request_close():
            messagebox.showinfo("PDF Viewing Optimizer", "The current file must finish before this window can close.", parent=root)
            return
        if inspection_process is not None and inspection_process.is_alive():
            inspection_process.terminate()
        closed=True;task_token+=1
        if poll_after is not None:
            root.after_cancel(poll_after);poll_after=None
        root.destroy()

    ttk.Label(controls,text='PDF mode:',underline=0).grid(row=0,column=0,sticky='w',padx=(0,6))
    profile_selector=ttk.Combobox(controls,textvariable=profile_choice,state='readonly',width=38,
                                values=tuple(PROFILE_LABELS.values()))
    profile_selector.grid(row=0,column=1,columnspan=5,sticky='w')
    optimizer_label = ttk.Label(controls,text='PDF optimizer:',underline=0)
    optimizer_label.grid(row=1,column=0,sticky='w',padx=(0,6),pady=(8,0))
    optimizer_selector=ttk.Combobox(controls,textvariable=optimizer_choice,state='readonly',width=38,
                                    values=tuple(OPTIMIZER_LABELS.values()))
    optimizer_selector.grid(row=1,column=1,columnspan=5,sticky='w',pady=(8,0))
    worker_label = ttk.Label(controls,text='Verification workers:')
    worker_label.grid(row=2,column=0,padx=(0,6),pady=(8,0))
    worker_selector.grid(row=2,column=1,padx=(0,12),pady=(8,0))
    parallel_selector=ttk.Checkbutton(controls,text='Parallel candidate generation (more RAM)',underline=19,
                                      variable=parallel_candidates)
    parallel_selector.grid(row=2,column=3,columnspan=3,sticky='e',pady=(8,0))
    study_paper=tk.StringVar(value=PAPER_LABELS['letter']);study_dpi=tk.StringVar(value='300')
    study_flatten_text=tk.BooleanVar(value=False)
    study_panel=ttk.LabelFrame(controls,text='Bitmap study copy (experimental)',padding=8)
    study_panel.grid(row=3,column=0,columnspan=6,sticky='ew',pady=(8,0))
    ttk.Label(study_panel,text='Paper size:').grid(row=0,column=0)
    study_paper_selector=ttk.Combobox(study_panel,textvariable=study_paper,values=tuple(PAPER_LABELS.values()),state='readonly',width=14)
    study_paper_selector.grid(row=0,column=1,padx=8)
    ttk.Label(study_panel,text='Resolution (DPI):').grid(row=0,column=2)
    study_dpi_selector=ttk.Combobox(study_panel,textvariable=study_dpi,values=('300','450','600'),state='readonly',width=6)
    study_dpi_selector.grid(row=0,column=3,padx=8)
    ttk.Label(study_panel,text='US Letter fits pages to standard US paper. Original size keeps their dimensions. Start at 300 DPI. Higher DPI shows finer detail and can make larger files.',wraplength=620).grid(row=1,column=0,columnspan=4,sticky='w')
    study_text_selector=ttk.Checkbutton(study_panel,text='Flatten text in the copy (loses search, selection and OCR)',
                                       variable=study_flatten_text)
    study_text_selector.grid(row=2,column=0,columnspan=4,sticky='w',pady=(6,0))
    note_mode=tk.StringVar(value=NOTE_WIDTH_LABELS['upstream']);note_width=tk.StringVar(value='0.10')
    note_unit=tk.StringVar(value='mm');note_scale=tk.StringVar(value='1.0')
    note_bookmarks=tk.BooleanVar(value=True);note_optimize=tk.BooleanVar(value=True)
    note_renderer=tk.StringVar()
    from note_to_pdf import find_renderer
    try:note_renderer.set(str(find_renderer()))
    except FileNotFoundError:pass
    notes_panel=ttk.LabelFrame(controls,text='BOOX notes (experimental)',padding=8)
    notes_panel.grid(row=5,column=0,columnspan=6,sticky='ew',pady=(8,0));notes_panel.columnconfigure(1,weight=1)
    ttk.Label(notes_panel,text='Converter:').grid(row=0,column=0,sticky='w')
    converter_entry=ttk.Entry(notes_panel,textvariable=note_renderer,width=52)
    converter_entry.grid(row=0,column=1,columnspan=3,sticky='ew')
    def browse_converter():
        selected=filedialog.askopenfilename(parent=root,title='Choose patched BOOX converter',filetypes=(('Executable','*.exe'),))
        if selected:note_renderer.set(selected)
    browse=ttk.Button(notes_panel,text='Browse…',command=browse_converter);browse.grid(row=0,column=4,padx=(8,0))
    ttk.Label(notes_panel,text='Pen widths:').grid(row=1,column=0,sticky='w',pady=6)
    modes=ttk.Combobox(notes_panel,textvariable=note_mode,values=tuple(NOTE_WIDTH_LABELS.values()),width=27,state='readonly')
    modes.grid(row=1,column=1,sticky='w')
    width=ttk.Spinbox(notes_panel,textvariable=note_width,from_=0.001,to=100,increment=0.01,width=8)
    width.grid(row=1,column=2,padx=6)
    units=ttk.Combobox(notes_panel,textvariable=note_unit,values=('mm','pt'),width=4,state='readonly');units.grid(row=1,column=3)
    scale=ttk.Spinbox(notes_panel,textvariable=note_scale,from_=0.001,to=100,increment=0.05,width=8);scale.grid(row=1,column=4)
    bookmarks=ttk.Checkbutton(notes_panel,text='Keep bookmarks',variable=note_bookmarks);bookmarks.grid(row=2,column=1,sticky='w')
    optimize=ttk.Checkbutton(notes_panel,text='Convert and Direct optimize',variable=note_optimize);optimize.grid(row=2,column=2,columnspan=3,sticky='w')
    ttk.Label(notes_panel,text='Custom widths change appearance. Density scaling is experimental. PDF settings above apply only to PDFs.').grid(row=3,column=0,columnspan=5,sticky='w')
    note_widgets=(converter_entry,browse,modes,width,units,scale,bookmarks,optimize)
    def update_note_controls(*_):
        if any(Path(p).suffix.lower()=='.note' for p in selected_paths):notes_panel.grid()
        else:notes_panel.grid_remove()
        modes.configure(state='readonly')
        width.configure(state='normal' if {label:key for key,label in NOTE_WIDTH_LABELS.items()}[note_mode.get()]=='fixed' else 'disabled')
        width.configure(from_=0.001 if note_unit.get()=='pt' else 0.001*25.4/72,to=100 if note_unit.get()=='pt' else 100*25.4/72)
        units.configure(state='readonly' if {label:key for key,label in NOTE_WIDTH_LABELS.items()}[note_mode.get()]=='fixed' else 'disabled')
        scale.configure(state='normal' if {label:key for key,label in NOTE_WIDTH_LABELS.items()}[note_mode.get()]=='scale' else 'disabled')
    note_mode.trace_add('write',update_note_controls)
    note_unit.trace_add('write',update_note_controls)
    update_note_controls()
    def update_optimizer_controls(*_args):
        quick=profile_choice.get()!=PROFILE_LABELS['thorough']
        if profile_choice.get()==PROFILE_LABELS['bitmap']:study_panel.grid()
        else:study_panel.grid_remove()
        for control in (optimizer_label, optimizer_selector, worker_label, worker_selector, parallel_selector):
            (control.grid_remove if quick else control.grid)()
        adaptive=not quick and optimizer_choice.get()==OPTIMIZER_LABELS['adaptive']
        if not adaptive:parallel_candidates.set(False)
        parallel_selector.configure(state='normal' if adaptive and not running else 'disabled')
        worker_selector.configure(state='normal' if not quick and not running else 'disabled')
        optimizer_selector.configure(state='readonly' if not quick and not running else 'disabled')
        update_resources()
    optimizer_choice.trace_add('write',update_optimizer_controls)
    choose_button=ttk.Button(actions,text='Choose files…',underline=7,command=choose,state='disabled')
    review_button=ttk.Button(actions,text='Review and run…',underline=0,command=plan_selected,state='disabled')
    setup_button=ttk.Button(actions,text='Check setup',underline=6,command=check_setup)
    inspect_button=ttk.Button(actions,text='Inspect PDF…',underline=0,command=inspect_pdf,state='disabled')
    review_inspected_button=ttk.Button(log_frame,text='Review inspected PDF…',state='disabled',
                                      command=lambda:plan_selected(inspected_pdf))
    review_inspected_button.grid(row=1,column=0,columnspan=2,sticky='e',pady=(6,0))
    review_inspected_button.grid_remove()
    stop_button=ttk.Button(actions,text='Stop after current',underline=0,command=stop,state='disabled')
    for button in (choose_button,review_button,setup_button,inspect_button,stop_button):
        button.pack(side='left',padx=(0,8) if button is not stop_button else 0)
    def change_profile(*_args):
        update_optimizer_controls();check_setup(False)
    profile_choice.trace_add('write',change_profile)
    update_optimizer_controls()
    root.bind('<Alt-s>',lambda _event:setup_button.invoke() if setup_button.instate(['!disabled']) else None)
    root.bind('<Alt-p>',lambda _event:choose() if str(choose_button['state'])!='disabled' else None)
    root.bind('<Alt-r>',lambda _event:review_button.invoke())
    root.bind('<Alt-i>',lambda _event:inspect_pdf() if str(inspect_button['state'])!='disabled' else None)
    root.bind('<Alt-t>',lambda _event:stop())
    root.bind('<Alt-g>',lambda _event:parallel_selector.invoke() if parallel_selector.instate(['!disabled']) else None)
    root.bind('<Alt-o>',lambda _event:optimizer_selector.focus_set() if optimizer_selector.instate(['!disabled']) else None)
    root.bind('<Alt-m>',lambda _event:profile_selector.focus_set() if profile_selector.instate(['!disabled']) else None)
    root.bind('<Control-l>',lambda _event:log.focus_set())
    def wrap_labels(event):
        if event.widget is root:
            for label in (selection_label,resource_label,current_label):
                label.configure(wraplength=max(360,event.width-60))
    root.bind('<Configure>',wrap_labels)
    def release_traces(event):
        nonlocal closed, poll_after
        if event.widget is root:
            closed=True
            if poll_after is not None:
                root.after_cancel(poll_after);poll_after=None
            for variable in (worker_count, optimizer_choice, note_mode, note_unit):
                for modes, callback in variable.trace_info():
                    variable.trace_remove(modes, callback)
    root.bind('<Destroy>',release_traces,add='+')
    root.protocol("WM_DELETE_WINDOW", close)
    poll_after=root.after(100, poll)
    root.after_idle(lambda:check_setup(False))
    root.mainloop()


if __name__ == "__main__":
    main()
