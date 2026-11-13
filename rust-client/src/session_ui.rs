//! Frontend commands for one Rust-owned local Luau session.
use crate::input_bridge::{InputBridge, InputPlan, OwnerAction};
use crate::render_assets::RenderAssetStore;
use crate::runtime_owner::{successful, Runtime};
use crate::ui_server::{random_ui_key, UiOperation};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub struct SessionUi {
    pub runtime: Runtime,
    input: InputBridge,
    identity: String,
    closing: bool,
    own_humanoid: Option<bool>,
    assets: Option<RenderAssetStore>,
}

impl SessionUi {
    pub fn new(runtime: Runtime) -> Result<Self, String> {
        let [keyboard, mouse] = runtime.original_input_devices();
        Ok(Self {
            runtime,
            input: InputBridge::new(Duration::from_secs(2), keyboard, mouse),
            identity: random_ui_key().map_err(|e| e.to_string())?,
            closing: false,
            own_humanoid: None,
            assets: None,
        })
    }
    pub fn set_content_root(&mut self, path: PathBuf) -> Result<(), String> {
        self.assets =
            Some(RenderAssetStore::new(path, 4 * 1024 * 1024).map_err(|e| e.to_string())?);
        Ok(())
    }
    pub fn is_closing(&self) -> bool {
        self.closing
    }

    pub fn dispatch(
        &mut self,
        operation: UiOperation,
        mut payload: Value,
    ) -> Result<Value, String> {
        if operation == UiOperation::Status {
            return Ok(json!({"state":if self.closing {"idle"} else {"local"},
                "game":{"name":"Local Luau session"},"events":[],"error":null,
                "editorUrl":"/session","live_game_session":false}));
        }
        if operation == UiOperation::RuntimeStatus {
            return Ok(
                json!({"connected":!self.closing,"session":if self.closing {None} else {Some(&self.identity)},
                "live_game_session":false,"runtime":"rust_in_process_official_luau"}),
            );
        }
        if operation == UiOperation::Events {
            return Ok(self.runtime.events(
                payload
                    .get("after")
                    .and_then(Value::as_u64)
                    .ok_or("Output cursor must be a nonnegative integer")?,
            ));
        }
        if operation == UiOperation::Leave {
            return self.close();
        }
        if self.closing {
            return Err("The local session has closed".into());
        }
        match operation {
            UiOperation::Execute => {
                let source = payload
                    .get_mut("source")
                    .ok_or("Source is required")?
                    .take();
                if !source.is_string() || source.as_str().unwrap().len() > 100 * 1024 * 1024 {
                    return Err("Source must be UTF-8 text up to 100 MiB".into());
                }
                let timeout = payload
                    .get("timeout_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(250);
                if !(1..=2000).contains(&timeout) {
                    return Err("CPU step budget must be 1..2000 ms".into());
                }
                let mut command = json!({"op":"execute","timeout_ms":timeout});
                command["source"] = source;
                self.runtime.call(command)
            }
            UiOperation::Render => {
                let result = self
                    .runtime
                    .render(payload.get("after").and_then(Value::as_u64))?;
                if let (Some(assets), Some(frame)) =
                    (&mut self.assets, self.runtime.render_projection())
                {
                    assets.observe(frame).map_err(|e| e.to_string())?;
                }
                Ok(result)
            }
            UiOperation::Gui => self
                .runtime
                .call(json!({"op":"gui_action","action":payload,"timeout_ms":20})),
            UiOperation::Keyboard => {
                let plan = self
                    .input
                    .plan_keyboard(&payload, Instant::now())
                    .map_err(|e| e.to_string())?;
                self.apply_plan(plan)
            }
            UiOperation::Display => {
                let action = InputBridge::plan_display(&payload).map_err(|e| e.to_string())?;
                self.apply_action(&action)?;
                Ok(json!({"ok":true,"display":payload,"state":"applied_local"}))
            }
            UiOperation::Movement => {
                self.own_humanoid = Some(self.runtime.own_humanoid_available()?);
                let plan = self
                    .input
                    .plan_movement(&payload, Instant::now(), self.own_humanoid)
                    .map_err(|e| e.to_string())?;
                self.apply_plan(plan)
            }
            UiOperation::Jump => {
                self.runtime.execute_control("local p=game:GetService('Players').LocalPlayer; local c=p and p.Character; local h=c and c:FindFirstChildOfClass('Humanoid'); if not h then error('Own character Humanoid is unavailable') end; h.Jump=true".into(),30)?;
                Ok(
                    json!({"ok":true,"values":[],"state":"accepted_local_only","server_movement_confirmed":false}),
                )
            }
            UiOperation::UserChat => {
                Err("Typed chat requires a server connection; this session is local".into())
            }
            UiOperation::RenderAsset => {
                let store = self
                    .assets
                    .as_mut()
                    .ok_or("Local content root is not configured")?;
                store
                    .get(
                        payload
                            .get("kind")
                            .and_then(Value::as_str)
                            .ok_or("Missing asset kind")?,
                        payload
                            .get("reference")
                            .and_then(Value::as_str)
                            .ok_or("Missing asset reference")?,
                        payload
                            .get("weight")
                            .and_then(Value::as_u64)
                            .map(|v| v as u16),
                        payload
                            .get("style")
                            .and_then(Value::as_u64)
                            .map(|v| v as u8),
                    )
                    .map_err(|e| e.to_string())
            }
            UiOperation::Status
            | UiOperation::RuntimeStatus
            | UiOperation::Events
            | UiOperation::Leave => unreachable!(),
        }
    }

    pub fn dispatch_console(&mut self, mut command: Value) -> Result<Value, String> {
        let op = command
            .get("op")
            .and_then(Value::as_str)
            .unwrap_or_else(|| {
                if command.get("source").is_some() {
                    "execute"
                } else {
                    ""
                }
            })
            .to_owned();
        match op.as_str() {
            "status" => self.runtime.status(
                command
                    .get("job_id")
                    .and_then(Value::as_u64)
                    .ok_or("status needs job_id")?,
            ),
            "runtime_status" => Ok(self.runtime.summary()),
            "events" => self.dispatch(UiOperation::Events, command),
            "execute" => self.dispatch(UiOperation::Execute, command),
            "render" => self.dispatch(UiOperation::Render, command),
            "gui_action" => self.dispatch(
                UiOperation::Gui,
                command
                    .get_mut("action")
                    .ok_or("GUI action is required")?
                    .take(),
            ),
            "keyboard" => self.dispatch(
                UiOperation::Keyboard,
                command
                    .get_mut("keyboard")
                    .ok_or("Keyboard input is required")?
                    .take(),
            ),
            "display" => self.dispatch(
                UiOperation::Display,
                command
                    .get_mut("display")
                    .ok_or("Display profile is required")?
                    .take(),
            ),
            "movement" => self.dispatch(
                UiOperation::Movement,
                command
                    .get_mut("movement")
                    .ok_or("Movement input is required")?
                    .take(),
            ),
            "leave" => self.close(),
            "poll" => self.tick(),
            _ => Err(format!("Unsupported local console operation: {op}")),
        }
    }

    fn apply_action(&mut self, action: &OwnerAction) -> Result<(), String> {
        let mut delta = json!({"version":1,"delta":true,"actions":[]});
        match action {
            OwnerAction::ExecuteLocalLuau { source, timeout_ms } => {
                self.runtime.execute_control(source.clone(), *timeout_ms)?;
                return Ok(());
            }
            OwnerAction::InputCapabilities(value) => delta["input_capabilities"] = value.clone(),
            OwnerAction::InputEvents(events) => delta["input_events"] = json!(events),
            OwnerAction::Display(value) => delta["display"] = value.clone(),
        }
        successful(
            &self
                .runtime
                .call(json!({"op":"update","delta":delta,"timeout_ms":20}))?,
        )
    }

    fn apply_plan(&mut self, plan: InputPlan) -> Result<Value, String> {
        for action in &plan.actions {
            self.apply_action(action)?;
        }
        let response = plan.response.clone();
        self.input.commit(plan);
        Ok(response)
    }

    pub fn tick(&mut self) -> Result<Value, String> {
        if self.closing {
            return Err("The local session has closed".into());
        }
        let expiry = self.input.plan_expiry(Instant::now(), self.own_humanoid);
        self.apply_plan(expiry)?;
        self.runtime.poll()
    }

    pub fn close(&mut self) -> Result<Value, String> {
        if !self.closing {
            let plan = self.input.plan_close(self.own_humanoid);
            let _ = self.apply_plan(plan);
            self.runtime.close();
            self.closing = true;
        }
        Ok(json!({"ok":true,"values":[],"closed":true,"live_game_session":false}))
    }
}
