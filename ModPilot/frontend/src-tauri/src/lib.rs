use std::fs::OpenOptions;
use std::io::Write;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

/// Owner of the spawned modpilot-backend.exe child process.
///
/// Held in app state so the window-close hook can reach it and terminate
/// the child cleanly, instead of orphaning uvicorn after the UI exits.
struct BackendChild(Mutex<Option<Child>>);

/// Port the backend actually bound to. Resolved at startup by probing a
/// range — the user's webview reads it via the `backend_port` command so
/// fetch URLs go to the right place even when 8000 is occupied by a
/// leaked socket from a previous crash.
struct BackendPort(Mutex<Option<u16>>);

const BACKEND_HOST: &str = "127.0.0.1";
const PORT_PROBE_START: u16 = 8000;
const PORT_PROBE_COUNT: u16 = 100;
const LOG_NAME: &str = "tauri-spawn.log";

/// Find the first free TCP port in `[start, start + count)`.
///
/// "Free" means a `TcpListener::bind` succeeds — we drop the listener
/// immediately so the about-to-be-spawned backend can take it. There IS
/// a TOCTOU window between our drop and uvicorn's bind, but on localhost
/// with no other process racing for ephemeral ports in this range it's
/// vanishingly small. The cost of a missed port is one redundant launch
/// failure, surfaced via tauri-spawn.log → user retries.
///
/// Returns `None` if every port in the range is occupied.
fn find_free_port(start: u16, count: u16) -> Option<u16> {
    for p in start..start.saturating_add(count) {
        if TcpListener::bind((BACKEND_HOST, p)).is_ok() {
            return Some(p);
        }
    }
    None
}

/// Tauri command — frontend reads this on boot to know which port to
/// hit for `fetch` / `EventSource`. Returns `PORT_PROBE_START` as a last
/// resort so the splash UI still has *something* to probe and can
/// surface a clear error.
#[tauri::command]
fn backend_port(state: tauri::State<BackendPort>) -> u16 {
    state.0.lock().unwrap().unwrap_or(PORT_PROBE_START)
}

/// Resolve the user-visible log directory and ensure it exists.
///
/// We use Tauri's `app_log_dir` (= `%LOCALAPPDATA%/com.modpilot.app/logs`
/// on Windows) so the child backend can co-locate its log there too.
/// Returning a Result lets callers fall back to `eprintln!` if the
/// filesystem is wedged, but in practice this never fails.
fn log_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_log_dir()
        .map_err(|e| format!("app_log_dir resolution failed: {e}"))?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir {} failed: {e}", dir.display()))?;
    Ok(dir)
}

/// Append a timestamped line to `<log_dir>/tauri-spawn.log`.
///
/// We open/close on every write — log volume is low (handful of lines per
/// app lifetime) and the alternative (a static Mutex<File>) would add
/// poisoning concerns without measurable benefit. Errors are swallowed
/// after eprintln so a failed log write can't take down the shell.
fn log_line(dir: &Path, msg: &str) {
    let line = format!("[{}] {msg}\n", chrono_now_or_secs());
    let path = dir.join(LOG_NAME);
    match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            let _ = f.write_all(line.as_bytes());
        }
        Err(e) => eprintln!("[modpilot] log write failed to {}: {e}", path.display()),
    }
    // Also mirror to stderr so debug builds and `cargo run` show it live.
    eprintln!("[modpilot] {msg}");
}

/// Lightweight timestamp without pulling in chrono. UTC, second
/// precision — enough to correlate Tauri events with backend.log.
fn chrono_now_or_secs() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("epoch={secs}")
}

/// Spawn the bundled pyinstaller-frozen backend on the chosen port.
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
fn spawn_backend(app: &tauri::AppHandle, logs: &Path, port: u16) -> Result<Child, String> {
    let resource_dir: PathBuf = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir resolution failed: {e}"))?;
    let exe = resource_dir.join("binaries/backend/modpilot-backend.exe");

    log_line(
        logs,
        &format!(
            "spawn: resource_dir={} exe={} exists={} port={port}",
            resource_dir.display(),
            exe.display(),
            exe.exists()
        ),
    );

    if !exe.exists() {
        return Err(format!(
            "backend exe not found at {} — did you run pyinstaller and copy \
             dist/modpilot-backend into src-tauri/binaries/backend/?",
            exe.display()
        ));
    }

    let mut cmd = Command::new(&exe);
    cmd.env("APP_HOST", BACKEND_HOST)
        .env("APP_PORT", port.to_string())
        // Backend mirrors its stdout/stderr + uvicorn logs to this dir.
        // Keeping both logs side-by-side simplifies "what happened?" triage.
        .env("MODPILOT_LOG_DIR", logs)
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
/// when the job handle drops. See win_job module docs for the full story.
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
        .manage(BackendChild(Mutex::new(None)))
        .manage(BackendPort(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![backend_port]);

    #[cfg(target_os = "windows")]
    let builder = builder.manage(JobHandle(std::sync::Mutex::new(None)));

    builder
        .setup(|app| {
            let logs = match log_dir(app.handle()) {
                Ok(d) => d,
                Err(e) => {
                    // No log dir means we lose visibility but can still
                    // attempt to spawn — fall through with a temp scratch.
                    eprintln!("[modpilot] log_dir setup failed: {e}");
                    std::env::temp_dir()
                }
            };
            log_line(
                &logs,
                &format!("=== tauri shell boot, log_dir={} ===", logs.display()),
            );

            // Probe for a free port before spawning. If the historical
            // default (8000) is occupied by a leaked socket from a prior
            // crash, fall forward to 8001, 8002, ... up to 8099.
            let port = match find_free_port(PORT_PROBE_START, PORT_PROBE_COUNT) {
                Some(p) => {
                    log_line(&logs, &format!("port probe: chose {p}"));
                    p
                }
                None => {
                    // Every port in the range is taken — bizarre, but
                    // surface it explicitly. The spawn will likely fail
                    // anyway; the log entry tells the user why.
                    log_line(
                        &logs,
                        &format!(
                            "port probe: NO free port in {PORT_PROBE_START}..{} — \
                             falling back to {PORT_PROBE_START}; spawn will likely fail",
                            PORT_PROBE_START + PORT_PROBE_COUNT
                        ),
                    );
                    PORT_PROBE_START
                }
            };

            // Publish chosen port so the webview can read it via the
            // `backend_port` Tauri command.
            *app.state::<BackendPort>().0.lock().unwrap() = Some(port);

            match spawn_backend(app.handle(), &logs, port) {
                Ok(child) => {
                    let pid = child.id();
                    log_line(
                        &logs,
                        &format!("spawn ok: pid={pid} target={BACKEND_HOST}:{port}"),
                    );

                    #[cfg(target_os = "windows")]
                    {
                        match assign_to_kill_on_close_job(&child) {
                            Ok(job) => {
                                log_line(
                                    &logs,
                                    "job binding ok: sidecar survives hard-kill of UI",
                                );
                                let job_state = app.state::<JobHandle>();
                                *job_state.0.lock().unwrap() = Some(job);
                            }
                            Err(err) => {
                                // Non-fatal — the soft-close path still works.
                                log_line(&logs, &format!("job binding failed: {err}"));
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
                    log_line(&logs, &format!("spawn skipped: {err}"));
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
                    let logs = log_dir(app).unwrap_or_else(|_| std::env::temp_dir());
                    match child.kill() {
                        Ok(()) => log_line(&logs, &format!("exit: backend terminated pid={pid}")),
                        Err(e) => {
                            log_line(&logs, &format!("exit: kill failed pid={pid} err={e}"))
                        }
                    }
                    let _ = child.wait();
                }
            }
        });
}
