use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

/// Owner of the spawned modpilot-backend.exe child process.
///
/// Held in app state so the window-close hook can reach it and terminate
/// the child cleanly, instead of orphaning uvicorn after the UI exits.
struct BackendChild(Mutex<Option<Child>>);

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: &str = "8000";

/// Spawn the bundled pyinstaller-frozen backend.
///
/// Production (bundled installer): backend lives at
/// `<install>/resources/binaries/backend/modpilot-backend.exe`, alongside
/// its pyinstaller `_internal/` lib folder. Pyinstaller's bootloader
/// resolves the lib path relative to `sys.executable`, so exe + _internal
/// must be co-located.
///
/// Dev (`pnpm tauri:dev`): the resource dir resolves under
/// `target/<profile>/`, but Tauri copies the bundled resources there too,
/// so the layout matches production.
fn spawn_backend(app: &tauri::AppHandle) -> Result<Child, String> {
    let resource_dir: PathBuf = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir resolution failed: {e}"))?;
    let exe = resource_dir.join("binaries/backend/modpilot-backend.exe");

    // Diagnostic breadcrumb (debug builds only) — release builds detach
    // stdout/stderr so eprintln is invisible. The log helps when a user
    // reports "the backend never started" — they can find the resolved
    // exe path at %TEMP%/modpilot-tauri-spawn.log.
    #[cfg(debug_assertions)]
    {
        let log_path = std::env::temp_dir().join("modpilot-tauri-spawn.log");
        let _ = std::fs::write(
            &log_path,
            format!(
                "resource_dir = {}\nexe path     = {}\nexe exists   = {}\n",
                resource_dir.display(),
                exe.display(),
                exe.exists()
            ),
        );
    }

    if !exe.exists() {
        return Err(format!(
            "backend exe not found at {} — did you run pyinstaller and copy \
             dist/modpilot-backend into src-tauri/binaries/backend/?",
            exe.display()
        ));
    }

    let mut cmd = Command::new(&exe);
    cmd.env("APP_HOST", BACKEND_HOST)
        .env("APP_PORT", BACKEND_PORT)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    // On Windows, suppress the secondary console window that would
    // otherwise pop up next to the WebView. CREATE_NO_WINDOW = 0x08000000.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }

    cmd.spawn().map_err(|e| format!("spawn failed: {e}"))
}

/// On Windows, bind the spawned child to a Job Object that kills the child
/// when the job handle drops. The OS drops the job handle when the parent
/// process exits — for any reason, including `taskkill /F`, OOM-kill, or a
/// crash — so the sidecar can never outlive the UI.
///
/// Without this, hard-killing modpilot.exe leaves modpilot-backend.exe
/// running, holding port 8000 and any open Blender socket. The graceful
/// shutdown path in `RunEvent::ExitRequested` only fires for soft closes
/// (window X button, alt+F4), so it can't cover crash scenarios.
///
/// Returns the Job handle which the caller MUST keep alive for the rest of
/// the process lifetime — dropping it early would close the job and kill
/// the child immediately.
#[cfg(target_os = "windows")]
mod win_job {
    //! Minimal inline FFI for the four kernel32 functions + one struct we
    //! need from `Win32::System::JobObjects`. Avoids pulling in
    //! `windows-sys`/`winapi` just for this — those crates add ~30 MB of
    //! transitive code at compile time.
    //!
    //! The struct layout and constants are stable Win32 ABI; they haven't
    //! changed since Windows XP. The chosen `JobObjectExtendedLimitInformation`
    //! class (value = 9) lets us set `KILL_ON_JOB_CLOSE`.

    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    type Handle = *mut c_void;
    type Bool = i32;

    pub const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x00002000;
    pub const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    // Mirrors the Win32 structs of the same name. Padding/sizing identical
    // because Rust uses C layout for #[repr(C)] and these are all integers
    // or pointer-sized values.
    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    #[derive(Default)]
    struct JobBasicLimitInfo {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct JobExtendedLimitInfo {
        basic_limit_information: JobBasicLimitInfo,
        io_info: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(security_attrs: *const c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(
            job: Handle,
            class: i32,
            info: *const c_void,
            len: u32,
        ) -> Bool;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> Bool;
        fn CloseHandle(handle: Handle) -> Bool;
    }

    /// Create a Job Object with `KILL_ON_JOB_CLOSE` set, assign `child` to
    /// it, and return the job handle. The OS kills the job (and every
    /// process in it) when the parent exits and the handle drops — which
    /// happens for *any* exit reason, including `taskkill /F` or a crash.
    ///
    /// The returned handle MUST be kept alive (stored in app state); if
    /// it's dropped early the OS closes the job immediately and the child
    /// dies right away.
    pub fn bind_to_kill_on_close(child: &Child) -> Result<isize, String> {
        // SAFETY: All four FFI calls are documented Win32 APIs. The struct
        // layout matches Win32 ABI. `child.as_raw_handle()` returns the
        // valid child process handle for the duration of `child`'s
        // lifetime, which outlives this function call.
        unsafe {
            let job: Handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err("CreateJobObjectW returned null".into());
            }

            let info = JobExtendedLimitInfo {
                basic_limit_information: JobBasicLimitInfo {
                    limit_flags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                    ..Default::default()
                },
                ..Default::default()
            };
            let info_size = std::mem::size_of::<JobExtendedLimitInfo>() as u32;
            let ok = SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                &info as *const _ as *const c_void,
                info_size,
            );
            if ok == 0 {
                CloseHandle(job);
                return Err("SetInformationJobObject failed".into());
            }

            let child_handle: Handle = child.as_raw_handle() as Handle;
            let ok = AssignProcessToJobObject(job, child_handle);
            if ok == 0 {
                CloseHandle(job);
                return Err("AssignProcessToJobObject failed".into());
            }

            Ok(job as isize)
        }
    }
}

/// On Windows, bind the spawned child to a Job Object that kills the child
/// when the job handle drops. The OS drops the job handle when the parent
/// process exits — for any reason, including `taskkill /F`, OOM-kill, or a
/// crash — so the sidecar can never outlive the UI.
///
/// Without this, hard-killing modpilot.exe leaves modpilot-backend.exe
/// running, holding port 8000 and any open Blender socket. The graceful
/// shutdown path in `RunEvent::ExitRequested` only fires for soft closes
/// (window X button, alt+F4), so it can't cover crash scenarios.
///
/// Returns the Job handle which the caller MUST keep alive for the rest of
/// the process lifetime — dropping it early would close the job and kill
/// the child immediately.
#[cfg(target_os = "windows")]
fn assign_to_kill_on_close_job(child: &Child) -> Result<isize, String> {
    win_job::bind_to_kill_on_close(child)
}

/// Holder for the Windows Job Object handle. Stored in app state for the
/// process lifetime so the OS keeps the job alive (and thus the
/// kill-on-close behavior active) until the parent exits.
#[cfg(target_os = "windows")]
struct JobHandle(std::sync::Mutex<Option<isize>>);

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendChild(Mutex::new(None)));

    #[cfg(target_os = "windows")]
    let builder = builder.manage(JobHandle(std::sync::Mutex::new(None)));

    builder
        .setup(|app| {
            match spawn_backend(app.handle()) {
                Ok(child) => {
                    eprintln!(
                        "[modpilot] backend started (pid {}) on {BACKEND_HOST}:{BACKEND_PORT}",
                        child.id()
                    );

                    #[cfg(target_os = "windows")]
                    {
                        match assign_to_kill_on_close_job(&child) {
                            Ok(job) => {
                                eprintln!("[modpilot] sidecar bound to job — survives hard-kill");
                                let job_state = app.state::<JobHandle>();
                                *job_state.0.lock().unwrap() = Some(job);
                            }
                            Err(err) => {
                                // Non-fatal — the soft-close path still works.
                                eprintln!("[modpilot] job binding failed: {err}");
                            }
                        }
                    }

                    let state = app.state::<BackendChild>();
                    *state.0.lock().unwrap() = Some(child);
                }
                Err(err) => {
                    // Don't abort startup — the user may be running the
                    // backend manually (dev workflow). The UI's healthcheck
                    // splash surfaces the issue if it's genuinely down.
                    eprintln!("[modpilot] backend spawn skipped: {err}");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Kill the sidecar on graceful app exit so uvicorn doesn't
            // orphan after the window closes. ExitRequested fires once per
            // app lifecycle. Hard-kills don't fire this — the Job Object
            // covers that path.
            if let RunEvent::ExitRequested { .. } = event {
                // Take the child out under the lock, then drop the guard
                // before operating on it — otherwise the MutexGuard borrow
                // extends through kill()/wait() and conflicts with
                // `state`'s lifetime.
                let taken = {
                    let state = app.state::<BackendChild>();
                    let mut guard = state.0.lock().unwrap();
                    let t = guard.take();
                    drop(guard);
                    t
                };
                if let Some(mut child) = taken {
                    let pid = child.id();
                    match child.kill() {
                        Ok(()) => eprintln!("[modpilot] backend terminated (pid {pid})"),
                        Err(e) => eprintln!("[modpilot] backend kill failed (pid {pid}): {e}"),
                    }
                    let _ = child.wait();
                }
            }
        });
}
