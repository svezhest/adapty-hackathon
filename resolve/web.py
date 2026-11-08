"""App factory: the JSON API (`resolve/api.py`) and the support web UI (static screens in `static/ui`)."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from resolve.models import Health
from resolve import api, config, db, insights, llm
from resolve.adapty import Client
from resolve.workflow import Services

HERE = Path(__file__).resolve().parent


def create_app(
    db_path: Path,
    demo: bool,
    adapty_client: Client | None = None,
    llm_client: llm.LLM | None = None,
    seed_file: Path = config.ROOT / "data" / "tickets.json",
    ab_runner: insights.Runner | None = None,
) -> FastAPI:
    conn = db.connect(db_path)
    if seed_file.exists():
        db.seed(conn, seed_file)
    services = Services(conn=conn, demo=demo, adapty=adapty_client, llm=llm_client)

    app = FastAPI(title="Resolve")
    app.state.services = services
    app.state.ab_runner = ab_runner
    api.install(app, services)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def ui_home():
        """The support web UI."""
        return RedirectResponse("/static/ui/Main.dc.html")

    @app.get("/impact", include_in_schema=False)
    def impact():
        """Market size, the numbers from the organisers' dataset, and what Resolve changes."""
        return RedirectResponse("/static/ui/Impact.dc.html")

    @app.get("/healthz", response_model=Health, tags=["mobile"])
    def healthz():
        return {"ok": True, "demo": demo}

    return app


def build_services_from_env() -> tuple[Path, bool, Client | None, llm.LLM | None, insights.Runner | None]:
    """What `python -m resolve` wires up: the snapshot copy in demo mode, the real clients otherwise."""
    if config.DEMO_MODE:
        runtime = config.ROOT / "data" / "demo-run.sqlite"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(runtime) + suffix).unlink(missing_ok=True)
        shutil.copyfile(config.DEMO_DB_PATH, runtime)
        return runtime, True, None, None, None
    adapty_client = Client(config.adapty_secret())
    llm_client = None
    try:
        llm_client = llm.make_llm()
    except config.MissingKey as exc:
        print(f"warning: {exc}; Level 1 triage, Level 2 chat and insights are disabled")
    return config.DB_PATH, False, adapty_client, llm_client, insights.Runner()
