"""Accessible Windows picker for sequential verified viewing-copy jobs."""

import queue
import threading
import time

from batch_workflow import (BatchController, confirmation_text, dependency_diagnostics,
                            dependency_report, plan_batch, resource_summary, system_resources)
from viewing_copy import default_verification_workers


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
    batch_total=0;batch_done=0
    heading=tkfont.nametofont('TkHeadingFont').copy();heading.configure(size=16,weight='bold')
    frame=ttk.Frame(root,padding=16);frame.grid(sticky='nsew');root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(5,weight=1)
    ttk.Label(frame,text='Verified viewing copies',font=heading).grid(row=0,column=0,sticky='w')
    ttk.Label(frame,text='Select PDFs from one or more folders, review every output path, then process one file at a time.',wraplength=760).grid(row=1,column=0,sticky='ew',pady=(3,12))
    resources=system_resources();worker_count=tk.IntVar(value=default_verification_workers())
    parallel_candidates=tk.BooleanVar(value=False)
    resource_text=tk.StringVar(value=resource_summary(worker_count.get(),resources))
    ttk.Label(frame,textvariable=resource_text,wraplength=760).grid(row=2,column=0,sticky='ew',pady=(0,8))
    current=tk.StringVar(value='Checking setup…');current_label=ttk.Label(frame,textvariable=current,wraplength=760)
    current_label.grid(row=3,column=0,sticky='ew',pady=(0,6))
    progress_row=ttk.Frame(frame);progress_row.grid(row=4,column=0,sticky='ew',pady=(0,10));progress_row.columnconfigure(0,weight=1)
    progress_status=tk.StringVar(value=progress_text(0,0));ttk.Label(progress_row,textvariable=progress_status).grid(row=0,column=0,sticky='w',pady=(0,3))
    progress=ttk.Progressbar(progress_row,mode='determinate',maximum=1,value=0);progress.grid(row=1,column=0,sticky='ew')
    log_frame=ttk.LabelFrame(frame,text='Activity log',padding=6);log_frame.grid(row=5,column=0,sticky='nsew')
    log_frame.columnconfigure(0,weight=1);log_frame.rowconfigure(0,weight=1)
    log=tk.Text(log_frame,wrap='word',state='disabled',takefocus=True);scroll=ttk.Scrollbar(log_frame,orient='vertical',command=log.yview)
    log.configure(yscrollcommand=scroll.set);log.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
    controls=ttk.Frame(frame);controls.grid(row=6,column=0,sticky='ew',pady=(12,0));controls.columnconfigure(2,weight=1)
    ttk.Label(controls,text='Verification workers:').grid(row=0,column=0,padx=(0,6))
    worker_selector=ttk.Spinbox(controls,from_=1,to=resources['logical_cpu_count'],width=5,
                                textvariable=worker_count,wrap=False)
    worker_selector.grid(row=0,column=1,padx=(0,12))
    def update_resources(*_args):
        try:value=worker_count.get()
        except tk.TclError:return
        resource_text.set(resource_summary(value,resources))
    worker_count.trace_add('write',update_resources)

    def set_status(text):
        current.set(text);root.title('PDF Viewing Optimizer — '+text[:80])

    def write(text):
        log.configure(state='normal');log.insert('end',text+'\n');log.see('end');log.configure(state='disabled')

    def check_setup(show_dialog=True):
        nonlocal setup_ready
        set_status('Checking setup…');setup_button.configure(state='disabled');choose_button.configure(state='disabled')
        rows=dependency_diagnostics();report=dependency_report(rows);write('\nSetup check\n'+report)
        blockers=[row for row in rows if not row['ok'] and not row['optional']]
        setup_ready=not blockers;setup_button.configure(state='normal')
        choose_button.configure(state='normal' if setup_ready and not running else 'disabled')
        set_status('Ready' if setup_ready else 'Setup needs attention')
        if show_dialog:(messagebox.showinfo if setup_ready else messagebox.showerror)('Setup check',report)
        return setup_ready

    def choose():
        nonlocal controller,running,active_source,active_stage,batch_total,batch_done
        if not setup_ready:
            messagebox.showerror('Setup needs attention','Run Check setup and follow the listed fix before selecting PDFs.')
            return
        selected=collect_pdf_paths(
            lambda:filedialog.askopenfilenames(title="Choose PDFs to add",filetypes=(("PDF files","*.pdf"),)),
            lambda count:messagebox.askyesno('Add PDFs from another folder?',
                f'{count} {"PDF" if count==1 else "PDFs"} selected. Add PDFs from another folder?'))
        if not selected:
            return
        jobs = plan_batch(selected)
        try:workers=worker_count.get()
        except tk.TclError:
            messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.')
            return
        if not 1<=workers<=resources['logical_cpu_count']:
            messagebox.showerror('Verification workers',f'Choose a whole number from 1 through {resources["logical_cpu_count"]}.')
            return
        parallel=parallel_candidates.get()
        controller = BatchController(jobs, lambda *event: events.put(event),verification_workers=workers,
                                     parallel_candidates=parallel)
        if not review_jobs(root,confirmation_text(jobs,workers,resources,parallel)):
            controller.cancel()
            write("Cancelled; no optimizer was started.")
            return
        if not any(job.status == "ready" for job in jobs):
            recovered=[job for job in jobs if job.status in {'optimized','no improvement'}]
            if recovered:
                write(f"Recovered {len(recovered)} completed result(s); nothing needs to run.")
                for job in recovered:write(f"{job.status.title()}: {job.source}")
                return
            write("No selected PDF passed preflight.")
            for job in jobs:
                write(f"Rejected: {job.source} — {job.error}")
            return
        running=True;active_source=None;active_stage=None;batch_done=0
        batch_total=sum(job.status=='ready' for job in jobs)
        progress.configure(maximum=max(1,batch_total),value=0);progress_status.set(progress_text(0,batch_total))
        choose_button.configure(state='disabled');setup_button.configure(state='disabled');stop_button.configure(state='normal')
        worker_selector.configure(state='disabled')
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
        nonlocal running,active_source,active_stage,batch_done
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "current":
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
                        write(f"No improvement; reports saved beside: {value.output.with_suffix('.json')}")
                    elif value.status in {"rejected", "failed", "timeout", "budget exhausted"}:
                        write(f"{value.status.title()}: {value.source} — {value.error}")
                    elif value.status == "skipped":
                        write(f"Skipped: {value.source} — {value.error}")
                elif kind == "complete":
                    running = False
                    active_stage = None
                    batch_done=batch_total;progress.configure(value=batch_done);progress_status.set(progress_text(batch_done,batch_total))
                    choose_button.configure(state="normal")
                    setup_button.configure(state="normal")
                    stop_button.configure(state="disabled")
                    worker_selector.configure(state='normal')
                    parallel_selector.configure(state='normal')
                    set_status("Complete")
                    counts = {status: sum(item.status == status for item in value)
                              for status in ("optimized", "no improvement", "failed", "timeout", "budget exhausted", "rejected", "skipped")}
                    summary = (f"Finished: {counts['optimized']} optimized, {counts['no improvement']} no improvement, "
                               f"{counts['failed']} failed, {counts['timeout']} timed out, {counts['budget exhausted']} over budget, "
                               f"{counts['rejected']} rejected, {counts['skipped']} skipped.")
                    write("\n" + summary)
                    messagebox.showinfo("PDF Viewing Optimizer", summary)
                    choose_button.focus_set()
        except queue.Empty:
            pass
        if running and active_stage:
            set_status(f"{active_source} — {active_stage[0]} ({time.monotonic()-active_stage[1]:.0f}s)")
        root.after(100, poll)

    def close():
        if controller and not controller.request_close():
            messagebox.showinfo("PDF Viewing Optimizer", "The current PDF must finish before this window can close.")
            return
        root.destroy()

    parallel_selector=ttk.Checkbutton(controls,text='Parallel candidate generation (more RAM)',underline=19,
                                      variable=parallel_candidates)
    parallel_selector.grid(row=0,column=3,columnspan=3,sticky='e')
    setup_button=ttk.Button(controls,text='Check setup',underline=6,command=check_setup)
    setup_button.grid(row=1,column=3,padx=(0,8),pady=(8,0))
    choose_button=ttk.Button(controls,text='Choose PDFs…',underline=7,command=choose,state='disabled')
    choose_button.grid(row=1,column=4,padx=(0,8),pady=(8,0))
    stop_button=ttk.Button(controls,text='Stop after current',underline=0,command=stop,state='disabled')
    stop_button.grid(row=1,column=5,pady=(8,0))
    root.bind('<Alt-s>',lambda _event:check_setup())
    root.bind('<Alt-p>',lambda _event:choose() if str(choose_button['state'])!='disabled' else None)
    root.bind('<Alt-t>',lambda _event:stop())
    root.bind('<Alt-g>',lambda _event:parallel_selector.invoke() if parallel_selector.instate(['!disabled']) else None)
    root.bind('<Control-l>',lambda _event:log.focus_set())
    root.bind('<Configure>',lambda event:current_label.configure(wraplength=max(360,event.width-60)) if event.widget is root else None)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.after_idle(lambda:check_setup(False))
    root.mainloop()


if __name__ == "__main__":
    main()
