"""Accessible Windows picker for sequential verified viewing-copy jobs."""

from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

from batch_workflow import (BatchController, OPTIMIZER_LABELS, PROFILE_LABELS, confirmation_text, dependency_diagnostics,
                            dependency_report, plan_batch, resource_summary, system_resources)


def progress_text(completed, total):
    return 'No batch running' if not total else f'{completed} of {total} files complete'


def collect_pdf_paths(pick,add_more):
    selected=[]
    while True:
        batch=pick()
        if not batch:return tuple(selected)
        selected.extend(batch)
        if not add_more(len(selected)):return tuple(selected)


def review_jobs(parent, text):
    import tkinter as tk
    from tkinter import ttk

    dialog=tk.Toplevel(parent);dialog.title('Review source and output paths');dialog.minsize(620,360)
    dialog.transient(parent);dialog.grab_set();dialog.columnconfigure(0,weight=1);dialog.rowconfigure(1,weight=1)
    ttk.Label(dialog,text='Review before starting',font='TkHeadingFont').grid(row=0,column=0,sticky='w',padx=16,pady=(16,8))
    body=ttk.Frame(dialog);body.grid(row=1,column=0,sticky='nsew',padx=16);body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
    paths=tk.Text(body,wrap='word',height=18,takefocus=True);scroll=ttk.Scrollbar(body,orient='vertical',command=paths.yview)
    paths.configure(yscrollcommand=scroll.set);paths.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
    paths.insert('1.0',text);paths.configure(state='disabled')
    controls=ttk.Frame(dialog);controls.grid(row=2,column=0,sticky='e',padx=16,pady=16)
    accepted={'value':False}
    def finish(value):accepted['value']=value;dialog.destroy()
    ttk.Button(controls,text='Cancel',command=lambda:finish(False)).grid(row=0,column=0,padx=(0,8))
    start=ttk.Button(controls,text='Start one at a time',underline=0,command=lambda:finish(True));start.grid(row=0,column=1)
    dialog.bind('<Escape>',lambda _event:finish(False));dialog.bind('<Alt-s>',lambda _event:finish(True))
    dialog.protocol('WM_DELETE_WINDOW',lambda:finish(False));start.focus_set();parent.wait_window(dialog)
    return accepted['value']


def main():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk

    root=tk.Tk();root.title('PDF Viewing Optimizer — checking setup');root.geometry('860x600');root.minsize(680,460)
    events=queue.Queue();controller=None;running=False;active_source=None;active_stage=None;setup_ready=False
    closed=False;task_token=0;poll_after=None
    batch_total=0;batch_done=0;batch_started=None
    heading=tkfont.nametofont('TkHeadingFont').copy();heading.configure(size=16,weight='bold')
    frame=ttk.Frame(root,padding=16);frame.grid(sticky='nsew');root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(5,weight=1)
    ttk.Label(frame,text='Verified viewing copies',font=heading).grid(row=0,column=0,sticky='w')
    ttk.Label(frame,text='Select PDFs from one or more folders, review every output path, then process one file at a time.',wraplength=760).grid(row=1,column=0,sticky='ew',pady=(3,12))
    resources=system_resources();worker_count=tk.IntVar(value=min(2,resources['logical_cpu_count']))
    parallel_candidates=tk.BooleanVar(value=False)
    profile_choice=tk.StringVar(value=PROFILE_LABELS['quick'])
    optimizer_choice=tk.StringVar(value=OPTIMIZER_LABELS['adaptive'])
    resource_text=tk.StringVar(value='Direct optimization: exact transformations and preservation checks, without optimizer comparisons or speed benchmarks.')
    ttk.Label(frame,textvariable=resource_text,wraplength=760).grid(row=2,column=0,sticky='ew',pady=(0,8))
    current=tk.StringVar(value='Checking setup…');current_label=ttk.Label(frame,textvariable=current,wraplength=760)
    current_label.grid(row=3,column=0,sticky='ew',pady=(0,6))
    progress_row=ttk.Frame(frame);progress_row.grid(row=4,column=0,sticky='ew',pady=(0,10));progress_row.columnconfigure(0,weight=1)
    progress_status=tk.StringVar(value=progress_text(0,0));ttk.Label(progress_row,textvariable=progress_status).grid(row=0,column=0,sticky='w',pady=(0,3))
    elapsed_status=tk.StringVar(value='Total elapsed: 0:00:00');ttk.Label(progress_row,textvariable=elapsed_status).grid(row=0,column=1,sticky='e',pady=(0,3))
    progress=ttk.Progressbar(progress_row,mode='determinate',maximum=1,value=0);progress.grid(row=1,column=0,sticky='ew')
    log_frame=ttk.LabelFrame(frame,text='Activity log',padding=6);log_frame.grid(row=5,column=0,sticky='nsew')
    log_frame.columnconfigure(0,weight=1);log_frame.rowconfigure(0,weight=1)
    log=tk.Text(log_frame,wrap='word',state='disabled',takefocus=True);scroll=ttk.Scrollbar(log_frame,orient='vertical',command=log.yview)
    log.configure(yscrollcommand=scroll.set);log.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
    controls=ttk.Frame(frame);controls.grid(row=6,column=0,sticky='ew',pady=(12,0));controls.columnconfigure(2,weight=1)
    worker_selector=ttk.Spinbox(controls,from_=1,to=resources['logical_cpu_count'],width=5,
                                textvariable=worker_count,wrap=False)
    def update_resources(*_args):
        if profile_choice.get()==PROFILE_LABELS['quick']:
            resource_text.set('Direct optimization keeps inlining, deduplication and cleanup. It verifies preservation without comparing optimizer timings.')
            return
        try:value=worker_count.get()
        except tk.TclError:return
        resource_text.set(resource_summary(value,resources))
    worker_count.trace_add('write',update_resources)

    def set_status(text):
        current.set(text);root.title('PDF Viewing Optimizer — '+text[:80])

    def write(text):
        log.configure(state='normal');log.insert('end',text+'\n');log.see('end');log.configure(state='disabled')

    def check_setup(show_dialog=True):
        nonlocal setup_ready,task_token
        if closed or running or setup_button.instate(['disabled']):return
        task_token+=1;token=task_token
        setup_ready=False
        set_status('Checking setup…');setup_button.configure(state='disabled');choose_button.configure(state='disabled')
        inspect_button.configure(state='disabled')
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
        choose_button.configure(state='disabled');setup_button.configure(state='disabled')
        inspect_button.configure(state='disabled');profile_selector.configure(state='disabled')
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
        nonlocal task_token
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before selecting PDFs.',parent=root)
            return
        selected=collect_pdf_paths(
            lambda:filedialog.askopenfilenames(parent=root,title="Choose PDFs to add",filetypes=(("PDF files","*.pdf"),)),
            lambda count:messagebox.askyesno('Add PDFs from another folder?',
                f'{count} {"PDF" if count==1 else "PDFs"} selected. Add PDFs from another folder?',parent=root))
        if not selected:
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
        parallel=parallel_candidates.get() if profile=='thorough' else False
        task_token+=1;token=task_token
        set_status('Inspecting selected PDFs…');choose_button.configure(state='disabled');setup_button.configure(state='disabled')
        inspect_button.configure(state='disabled');profile_selector.configure(state='disabled')
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
        controller = BatchController(jobs, lambda *event: events.put(event),verification_workers=workers,
                                     parallel_candidates=parallel)
        if not review_jobs(root,confirmation_text(jobs,workers,resources,parallel)):
            controller.cancel()
            write("Cancelled; no optimizer was started.")
            setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
            choose_button.configure(state='normal' if setup_ready else 'disabled')
            inspect_button.configure(state='normal' if setup_ready else 'disabled')
            set_status('Ready')
            return
        if not any(job.status == "ready" for job in jobs):
            recovered=[job for job in jobs if job.status in {'optimized','no improvement'}]
            if recovered:
                write(f"Recovered {len(recovered)} completed result(s); nothing needs to run.")
                for job in recovered:write(f"{job.status.title()}: {job.source}")
                choose_button.configure(state='normal');inspect_button.configure(state='normal');set_status('Ready')
                return
            write("No selected PDF passed preflight.")
            for job in jobs:
                write(f"Rejected: {job.source} — {job.error}")
            choose_button.configure(state='normal');inspect_button.configure(state='normal');set_status('Ready')
            return
        running=True;active_source=None;active_stage=None;batch_done=0;batch_started=time.monotonic();elapsed_status.set('Total elapsed: 0:00:00')
        batch_total=sum(job.status=='ready' for job in jobs)
        progress.configure(maximum=max(1,batch_total),value=0);progress_status.set(progress_text(0,batch_total))
        choose_button.configure(state='disabled');setup_button.configure(state='disabled');stop_button.configure(state='normal')
        inspect_button.configure(state='disabled')
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
        try:
            while True:
                kind, value = events.get_nowait()
                try:
                    if kind == 'setup complete':
                        token,show_dialog,rows=value
                        if token!=task_token or closed:continue
                        report=dependency_report(rows);write('\nSetup check\n'+report)
                        blockers=[row for row in rows if not row['ok'] and not row['optional']]
                        setup_ready=not blockers;setup_button.configure(state='normal')
                        profile_selector.configure(state='readonly');update_optimizer_controls()
                        choose_button.configure(state='normal' if setup_ready and not running else 'disabled')
                        inspect_button.configure(state='normal' if setup_ready and not running else 'disabled')
                        set_status('Ready' if setup_ready else 'Setup needs attention')
                        if show_dialog:(messagebox.showinfo if setup_ready else messagebox.showerror)('Setup check',report,parent=root)
                    elif kind == 'setup failed':
                        token,show_dialog,error=value
                        if token!=task_token or closed:continue
                        setup_ready=False;setup_button.configure(state='normal');profile_selector.configure(state='readonly')
                        inspect_button.configure(state='disabled')
                        update_optimizer_controls();set_status('Setup needs attention')
                        write('Setup check failed: '+str(error))
                        if show_dialog:messagebox.showerror('Setup check',str(error),parent=root)
                    elif kind == 'planning progress':
                        token,path=value
                        if token==task_token and not closed:set_status('Inspecting '+path)
                    elif kind == 'planning complete':
                        token,jobs,workers,parallel=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        review_planned(jobs,workers,parallel)
                    elif kind == 'planning failed':
                        token,error=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        choose_button.configure(state='normal' if setup_ready else 'disabled')
                        inspect_button.configure(state='normal' if setup_ready else 'disabled')
                        set_status('Selection failed');write('Selection failed: '+str(error));messagebox.showerror('Selection failed',str(error),parent=root)
                    elif kind == 'inspection complete':
                        token,path,report=value
                        if token!=task_token or closed:continue
                        setup_button.configure(state='normal');profile_selector.configure(state='readonly');update_optimizer_controls()
                        choose_button.configure(state='normal' if setup_ready else 'disabled')
                        inspect_button.configure(state='normal' if setup_ready else 'disabled')
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
                        choose_button.configure(state='normal' if setup_ready else 'disabled')
                        inspect_button.configure(state='normal' if setup_ready else 'disabled')
                        set_status('Inspection failed')
                        write(f"\nInspection failed for {path}: {error}")
                        messagebox.showerror('Inspection failed',str(error),parent=root)
                    elif kind == "current":
                        active_source = value.source
                        set_status(str(value.source))
                        write("\n" + str(value.source))
                    elif kind == "output":
                        write(value)
                    elif kind == "stage":
                        active_stage = value
                    elif kind == "result":
                        batch_done=min(batch_total,batch_done+1);progress.configure(value=batch_done)
                        progress_status.set(progress_text(batch_done,batch_total))
                        if value.status == "optimized":
                            write(f"Saved: {value.output}")
                        elif value.status == "no improvement":
                            write(f"No PDF published; see the report for the reason: {value.output.parent}")
                        elif value.status in {"rejected", "failed", "timeout", "budget exhausted"}:
                            write(f"{value.status.title()}: {value.source} — {value.error}")
                        elif value.status == "skipped":
                            write(f"Skipped: {value.source} — {value.error}")
                    elif kind == "complete":
                        running = False
                        elapsed=time.monotonic()-batch_started if batch_started is not None else 0
                        elapsed_status.set(f'Total elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
                        active_stage = None
                        batch_done=batch_total;progress.configure(value=batch_done);progress_status.set(progress_text(batch_done,batch_total))
                        choose_button.configure(state="normal")
                        setup_button.configure(state="normal")
                        inspect_button.configure(state="normal")
                        stop_button.configure(state="disabled")
                        worker_selector.configure(state='normal')
                        optimizer_selector.configure(state='readonly')
                        profile_selector.configure(state='readonly')
                        update_optimizer_controls()
                        set_status("Complete")
                        counts = {status: sum(item.status == status for item in value)
                                  for status in ("optimized", "no improvement", "failed", "timeout", "budget exhausted", "rejected", "skipped")}
                        summary = (f"Finished: {counts['optimized']} optimized, {counts['no improvement']} no improvement, "
                                   f"{counts['failed']} failed, {counts['timeout']} timed out, {counts['budget exhausted']} over budget, "
                                   f"{counts['rejected']} rejected, {counts['skipped']} skipped.")
                        write("\n" + summary)
                        messagebox.showinfo("PDF Viewing Optimizer", summary, parent=root)
                        choose_button.focus_set()
                except Exception as exc:
                    write(f"UI event handling error ({kind}): {exc}")
        except queue.Empty:
            pass
        finally:
            if running and active_stage:
                elapsed=time.monotonic()-batch_started if batch_started is not None else 0
                elapsed_status.set(f'Total elapsed: {time.strftime("%H:%M:%S",time.gmtime(elapsed))}')
                set_status(f"{active_source} — {active_stage[0]} ({time.monotonic()-active_stage[1]:.0f}s)")
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
    inspect_button=ttk.Button(controls,text='Inspect PDF…',underline=0,command=inspect_pdf,state='disabled')
    inspect_button.grid(row=3,column=2,sticky='e',padx=(0,8),pady=(8,0))
    setup_button=ttk.Button(controls,text='Check setup',underline=6,command=check_setup)
    setup_button.grid(row=3,column=3,padx=(0,8),pady=(8,0))
    choose_button=ttk.Button(controls,text='Choose PDFs…',underline=7,command=choose,state='disabled')
    choose_button.grid(row=3,column=4,padx=(0,8),pady=(8,0))
    stop_button=ttk.Button(controls,text='Stop after current',underline=0,command=stop,state='disabled')
    stop_button.grid(row=3,column=5,pady=(8,0))
    def change_profile(*_args):
        update_optimizer_controls();check_setup(False)
    profile_choice.trace_add('write',change_profile)
    update_optimizer_controls()
    root.bind('<Alt-s>',lambda _event:setup_button.invoke() if setup_button.instate(['!disabled']) else None)
    root.bind('<Alt-p>',lambda _event:choose() if str(choose_button['state'])!='disabled' else None)
    root.bind('<Alt-i>',lambda _event:inspect_pdf() if str(inspect_button['state'])!='disabled' else None)
    root.bind('<Alt-t>',lambda _event:stop())
    root.bind('<Alt-g>',lambda _event:parallel_selector.invoke() if parallel_selector.instate(['!disabled']) else None)
    root.bind('<Alt-o>',lambda _event:optimizer_selector.focus_set() if optimizer_selector.instate(['!disabled']) else None)
    root.bind('<Alt-m>',lambda _event:profile_selector.focus_set() if profile_selector.instate(['!disabled']) else None)
    root.bind('<Control-l>',lambda _event:log.focus_set())
    root.bind('<Configure>',lambda event:current_label.configure(wraplength=max(360,event.width-60)) if event.widget is root else None)
    root.protocol("WM_DELETE_WINDOW", close)
    poll_after=root.after(100, poll)
    root.after_idle(lambda:check_setup(False))
    root.mainloop()


if __name__ == "__main__":
    main()
