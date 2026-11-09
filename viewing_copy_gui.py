"""Accessible Windows picker for sequential verified viewing-copy jobs."""

import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

from batch_workflow import (BatchController, OPTIMIZER_LABELS, PROFILE_LABELS, confirmation_text, dependency_diagnostics,
                            dependency_report, plan_batch, resource_summary, system_resources)

STAGE_PROGRESS = {
    'quick input checks': (2, 5),
    'qpdf preflight': (5, 10),
    'renderer and policy context': (10, 18),
    'exact inlining, deduplication and cleanup': (18, 30),
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
    'publication': (98, 99),
}
VERIFIED_PAGES = re.compile(
    r'(?:Verified vector content/navigation/appearance|PDF\.js appearance verified): (\d+)/(\d+)')


def stage_progress(stage, previous):
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


def review_jobs(parent, text):
    return batch_dialog(parent,text)


def batch_dialog(parent, text, saved_paths=None):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import messagebox, ttk

    reviewing=saved_paths is None
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
    if saved_paths:
        paths.insert('end','\nSaved locations\n',('heading',))
        def open_folder(output):
            try:os.startfile(str(output.parent))
            except OSError as exc:messagebox.showerror('Could not open folder',str(exc),parent=dialog)
        for output in saved_paths:
            paths.insert('end',f'\n{output}\n')
            if os.name=='nt':
                button=ttk.Button(paths,text='Open folder',command=lambda output=output:open_folder(output))
                paths.window_create('end',window=button)
                paths.insert('end','\n')
    paths.configure(state='disabled')
    controls=ttk.Frame(dialog);controls.grid(row=2,column=0,sticky='e',padx=16,pady=16)
    accepted={'value':False}
    def finish(value):accepted['value']=value;dialog.destroy()
    if reviewing:
        ttk.Button(controls,text='Back to settings',command=lambda:finish(False)).grid(row=0,column=0,padx=(0,8))
        primary=ttk.Button(controls,text='Start one at a time',underline=0,command=lambda:finish(True))
        dialog.bind('<Alt-s>',lambda _event:finish(True))
    else:
        primary=ttk.Button(controls,text='Close',command=lambda:finish(False))
    primary.grid(row=0,column=1)
    dialog.bind('<Escape>',lambda _event:finish(False))
    dialog.protocol('WM_DELETE_WINDOW',lambda:finish(False))
    dialog.geometry(f'+{parent.winfo_x()+28}+{parent.winfo_y()+28}')
    dialog.deiconify();dialog.grab_set();primary.focus_set();parent.wait_window(dialog)
    return accepted['value']


def main():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk

    root=tk.Tk();root.title('PDF Viewing Optimizer — checking setup');root.geometry('860x600');root.minsize(680,460)
    events=queue.Queue();controller=None;running=False;active_source=None;active_stage=None;setup_ready=False
    closed=False;task_token=0;poll_after=None
    batch_total=0;batch_done=0;batch_started=None
    file_progress=stage_start=stage_end=0
    selected_paths=()
    heading=tkfont.nametofont('TkHeadingFont').copy();heading.configure(size=16,weight='bold')
    bold=tkfont.nametofont('TkDefaultFont').copy();bold.configure(weight='bold')
    frame=ttk.Frame(root,padding=16);frame.grid(sticky='nsew');root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(5,weight=1)
    ttk.Label(frame,text='Verified viewing copies',font=heading).grid(row=0,column=0,sticky='w')
    selection_text=tk.StringVar(value='Choose PDFs, adjust settings, then review the batch before starting.')
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
    actions=ttk.Frame(controls);actions.grid(row=3,column=0,columnspan=6,sticky='e',pady=(8,0))
    worker_selector=ttk.Spinbox(controls,from_=1,to=resources['logical_cpu_count'],width=5,
                                textvariable=worker_count,wrap=False)
    def update_resources(*_args):
        if profile_choice.get()==PROFILE_LABELS['quick']:
            resource_text.set('Direct checks preservation without measuring viewing speed.')
            return
        try:value=worker_count.get()
        except tk.TclError:return
        resource_text.set(resource_summary(value,resources,compact=True))
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

    def check_setup(show_dialog=True):
        nonlocal setup_ready,task_token
        if closed or running or setup_button.instate(['disabled']):return
        task_token+=1;token=task_token
        setup_ready=False
        set_status('Checking setup…');setup_button.configure(state='disabled');enable_inputs(False)
        profile={label:key for key,label in PROFILE_LABELS.items()}[profile_choice.get()]
        profile_selector.configure(state='disabled');optimizer_selector.configure(state='disabled')
        worker_selector.configure(state='disabled');parallel_selector.configure(state='disabled')
        def work():
            try:events.put(('setup complete',(token,show_dialog,dependency_diagnostics(profile))))
            except Exception as exc:events.put(('setup failed',(token,show_dialog,exc)))
        threading.Thread(target=work,daemon=True).start()

    def inspect_pdf():
        nonlocal task_token
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before inspecting PDFs.',parent=root)
            return
        selected=filedialog.askopenfilename(parent=root,title="Choose PDF to inspect drawing opportunities",
                                            filetypes=(("PDF files","*.pdf"),))
        if not selected:return
        task_token+=1;token=task_token
        set_status('Inspecting drawing opportunities…')
        write(f"\nInspecting drawing opportunities: {selected}…")
        enable_inputs(False);setup_button.configure(state='disabled')
        profile_selector.configure(state='disabled')
        optimizer_selector.configure(state='disabled');worker_selector.configure(state='disabled')
        parallel_selector.configure(state='disabled')
        def work():
            try:
                from experiment_manifest import analyze_drawing_opportunity
                report=analyze_drawing_opportunity(selected)
                events.put(('inspection complete',(token,selected,report)))
            except Exception as exc:events.put(('inspection failed',(token,selected,exc)))
        threading.Thread(target=work,daemon=True).start()

    def choose():
        nonlocal selected_paths
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before selecting PDFs.',parent=root)
            return
        selected=collect_pdf_paths(
            lambda:filedialog.askopenfilenames(parent=root,title="Choose PDFs to add",filetypes=(("PDF files","*.pdf"),)),
            lambda count:messagebox.askyesno('Add PDFs from another folder?',
                f'{count} {"PDF" if count==1 else "PDFs"} selected. Add PDFs from another folder?',parent=root))
        if not selected:
            return
        selected_paths=tuple(dict.fromkeys(selected))
        count=len(selected_paths)
        selection_text.set(f'{count} {"PDF" if count==1 else "PDFs"} selected. Adjust settings, then choose Review batch.')
        write('\nSelected PDFs',heading=True)
        write('\n'.join(selected_paths))
        enable_inputs(True)
        review_button.focus_set()

    def plan_selected():
        nonlocal task_token
        selected=selected_paths
        if not selected or not setup_ready:
            return
        profile={label:key for key,label in PROFILE_LABELS.items()}[profile_choice.get()]
        optimizer='adaptive' if profile=='quick' else {label:key for key,label in OPTIMIZER_LABELS.items()}[optimizer_choice.get()]
        workers=None
        if profile=='thorough':
            try:workers=worker_count.get()
            except tk.TclError:
                messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.',parent=root)
                return
            if not 1<=workers<=resources['logical_cpu_count']:
                messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.',parent=root)
                return
        parallel=profile=='thorough' and optimizer=='adaptive' and parallel_candidates.get()
        task_token+=1;token=task_token
        set_status('Inspecting selected PDFs…');enable_inputs(False);setup_button.configure(state='disabled')
        profile_selector.configure(state='disabled');optimizer_selector.configure(state='disabled')
        worker_selector.configure(state='disabled');parallel_selector.configure(state='disabled')
        def work():
            try:
                jobs=plan_batch(selected,optimizer,profile,
                                lambda path:events.put(('planning progress',(token,str(path)))))
                events.put(('planning complete',(token,jobs,workers,parallel)))
            except Exception as exc:events.put(('planning failed',(token,exc)))
        threading.Thread(target=work,daemon=True).start()

    def review_planned(jobs,workers,parallel):
        nonlocal controller,running,active_source,active_stage,batch_total,batch_done,batch_started
        nonlocal selected_paths,file_progress
        if not review_jobs(root,confirmation_text(jobs,workers,resources,parallel)):
            write("Selection kept. Adjust settings and review again when ready.")
            setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
            enable_inputs(setup_ready)
            set_status('Ready')
            review_button.focus_set()
            return
        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
        enable_inputs(setup_ready)
        if not any(job.status == "ready" for job in jobs):
            recovered=[job for job in jobs if job.status in {'optimized','no improvement','failed'}]
            if recovered:
                write(f"Recovered {len(recovered)} completed result(s); nothing needs to run.")
                for job in recovered:
                    detail = f" — {job.error}" if job.error else ""
                    write(f"{job.status.title()}: {job.source}{detail}")
                set_status('Ready')
                return
            write("No selected PDF passed preflight.")
            for job in jobs:
                write(f"Rejected: {job.source} — {job.error}")
            set_status('Ready')
            return
        controller = BatchController(jobs, lambda *event: events.put(event),verification_workers=workers,
                                     parallel_candidates=parallel)
        selected_paths=()
        selection_text.set('Choose PDFs, adjust settings, then review the batch before starting.')
        running=True;active_source=None;active_stage=None;batch_done=0;batch_started=time.monotonic();elapsed_status.set('Total elapsed: 0:00:00')
        batch_total=sum(job.status=='ready' for job in jobs)
        file_progress=0
        progress.configure(maximum=batch_total);update_progress()
        enable_inputs(False);setup_button.configure(state='disabled');stop_button.configure(state='normal')
        worker_selector.configure(state='disabled')
        optimizer_selector.configure(state='disabled')
        profile_selector.configure(state='disabled')
        parallel_selector.configure(state='disabled')
        write(f"Reviewed {len(jobs)} unique PDF(s).")

        def work():
            results = controller.run()
            events.put(("complete", results))

        threading.Thread(target=work, daemon=True).start()

    def stop():
        if controller and running:
            controller.request_stop()
            stop_button.configure(state="disabled")
            write("Stop requested; the current PDF will finish and queued PDFs will be skipped.")

    def poll():
        nonlocal running,active_source,active_stage,batch_done,batch_started,setup_ready,poll_after
        nonlocal file_progress,stage_start,stage_end
        try:
            while True:
                kind, value = events.get_nowait()
                try:
                    if kind == 'setup complete':
                        token,show_dialog,rows=value
                        if token!=task_token or closed:continue
                        report=dependency_report(rows);write('\nSetup check',heading=True);write(report)
                        blockers=[row for row in rows if not row['ok'] and not row['optional']]
                        setup_ready=not blockers;setup_button.configure(state='normal')
                        profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready and not running)
                        set_status('Ready' if setup_ready else 'Setup needs attention')
                        if show_dialog:(messagebox.showinfo if setup_ready else messagebox.showerror)('Setup check',report,parent=root)
                    elif kind == 'setup failed':
                        token,show_dialog,error=value
                        if token!=task_token or closed:continue
                        setup_ready=False;setup_button.configure(state='normal');profile_selector.configure(state='readonly')
                        enable_inputs(False)
                        update_optimizer_controls();set_status('Setup needs attention')
                        write('Setup check failed: '+str(error))
                        if show_dialog:messagebox.showerror('Setup check',str(error),parent=root)
                    elif kind == 'planning progress':
                        token,path=value
                        if token==task_token and not closed:set_status('Checking '+Path(path).name)
                    elif kind == 'planning complete':
                        token,jobs,workers,parallel=value
                        if token!=task_token or closed:continue
                        review_planned(jobs,workers,parallel)
                    elif kind == 'planning failed':
                        token,error=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready)
                        set_status('Selection failed');write('Selection failed: '+str(error));messagebox.showerror('Selection failed',str(error),parent=root)
                    elif kind == 'inspection complete':
                        token,path,report=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready)
                        set_status('Ready')
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
                            f"  Declared line widths: {', '.join(str(w) for w in inv['local_widths']) if inv['local_widths'] else 'none'}",
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
                    elif kind == 'inspection failed':
                        token,path,error=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        enable_inputs(setup_ready)
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
                        stage_start,stage_end=stage_progress(value[0],file_progress)
                        file_progress=max(file_progress,stage_start)
                        update_progress()
                    elif kind == "result":
                        active_stage=None
                        file_progress=0
                        batch_done=min(batch_total,batch_done+1);update_progress()
                        if value.status == "optimized":
                            write(f"Saved copy: {value.output}")
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
                        labels = {"optimized":"Copies saved", "no improvement":"No improvement",
                                  "failed":"Failed", "timeout":"Timed out", "budget exhausted":"Over budget",
                                  "rejected":"Rejected", "skipped":"Skipped"}
                        counts = [f"{label}: {count}" for status,label in labels.items()
                                  if (count := sum(item.status == status for item in value))]
                        summary = "\n".join(counts)
                        failures = [f"{item.source.name} — {item.error}" for item in value
                                    if item.status == "failed" and item.error]
                        if failures:
                            summary += "\n\n" + "\n".join(failures)
                        write("\nBatch complete",heading=True)
                        write(summary)
                        saved_paths=tuple(item.output for item in value if item.status=="optimized")
                        if saved_paths:
                            write("\nSaved locations",heading=True)
                            write("\n".join(map(str,saved_paths)))
                        batch_dialog(root,summary,saved_paths=saved_paths)
                        choose_button.focus_set()
                except Exception as exc:
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
            if not closed:poll_after=root.after(100, poll)

    def close():
        nonlocal closed,task_token,poll_after
        if controller and not controller.request_close():
            messagebox.showinfo("PDF Viewing Optimizer", "The current PDF must finish before this window can close.", parent=root)
            return
        closed=True;task_token+=1
        if poll_after is not None:
            root.after_cancel(poll_after);poll_after=None
        root.destroy()

    ttk.Label(controls,text='Mode:',underline=0).grid(row=0,column=0,sticky='w',padx=(0,6))
    profile_selector=ttk.Combobox(controls,textvariable=profile_choice,state='readonly',width=38,
                                values=tuple(PROFILE_LABELS.values()))
    profile_selector.grid(row=0,column=1,columnspan=5,sticky='w')
    ttk.Label(controls,text='Optimizer:',underline=0).grid(row=1,column=0,sticky='w',padx=(0,6),pady=(8,0))
    optimizer_selector=ttk.Combobox(controls,textvariable=optimizer_choice,state='readonly',width=38,
                                    values=tuple(OPTIMIZER_LABELS.values()))
    optimizer_selector.grid(row=1,column=1,columnspan=5,sticky='w',pady=(8,0))
    ttk.Label(controls,text='Verification workers:').grid(row=2,column=0,padx=(0,6),pady=(8,0))
    worker_selector.grid(row=2,column=1,padx=(0,12),pady=(8,0))
    parallel_selector=ttk.Checkbutton(controls,text='Parallel candidate generation (more RAM)',underline=19,
                                      variable=parallel_candidates)
    parallel_selector.grid(row=2,column=3,columnspan=3,sticky='e',pady=(8,0))
    def update_optimizer_controls(*_args):
        quick=profile_choice.get()==PROFILE_LABELS['quick']
        adaptive=not quick and optimizer_choice.get()==OPTIMIZER_LABELS['adaptive']
        if not adaptive:parallel_candidates.set(False)
        parallel_selector.configure(state='normal' if adaptive and not running else 'disabled')
        worker_selector.configure(state='normal' if not quick and not running else 'disabled')
        optimizer_selector.configure(state='readonly' if not quick and not running else 'disabled')
        update_resources()
    optimizer_choice.trace_add('write',update_optimizer_controls)
    inspect_button=ttk.Button(actions,text='Inspect PDF…',underline=0,command=inspect_pdf,state='disabled')
    setup_button=ttk.Button(actions,text='Check setup',underline=6,command=check_setup)
    choose_button=ttk.Button(actions,text='Choose PDFs…',underline=7,command=choose,state='disabled')
    review_button=ttk.Button(actions,text='Review batch…',underline=0,command=plan_selected,state='disabled')
    stop_button=ttk.Button(actions,text='Stop after current',underline=0,command=stop,state='disabled')
    for button in (inspect_button,setup_button,choose_button,review_button,stop_button):
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
    root.protocol("WM_DELETE_WINDOW", close)
    poll_after=root.after(100, poll)
    root.after_idle(lambda:check_setup(False))
    root.mainloop()


if __name__ == "__main__":
    main()
