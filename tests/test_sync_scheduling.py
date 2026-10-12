from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_scheduler_is_weekly_monday_eight_and_ignores_overlaps():
    script = (ROOT / "scripts" / "register_weekly_sync_task.ps1").read_text(encoding="utf-8")
    assert "-DaysOfWeek Monday" in script
    assert "08:00" in script
    assert "China Standard Time" in script
    assert "Get-TimeZone" in script
    assert "MultipleInstances IgnoreNew" in script
    assert "StudyCompassOfficialSync" in script
    runner = (ROOT / "scripts" / "run_weekly_sync.ps1").read_text(encoding="utf-8")
    assert "sync_program_sources" in runner


def test_scheduler_scripts_resolve_project_paths_and_do_not_embed_secrets():
    register = (ROOT / "scripts" / "register_weekly_sync_task.ps1").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run_weekly_sync.ps1").read_text(encoding="utf-8")
    remover = (ROOT / "scripts" / "remove_weekly_sync_task.ps1").read_text(encoding="utf-8")
    combined = register + runner + remover
    assert "$PSScriptRoot" in combined
    assert ".venv" in runner
    assert "logs" in runner
    assert "Unregister-ScheduledTask" in remover
    assert "AI_API_KEY=" not in combined
    assert "ADMIN_TOKEN=" not in combined
    assert "--all" in runner


def test_readme_documents_manual_weekly_install_removal_logs_and_linux_cron():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "register_weekly_sync_task.ps1", "remove_weekly_sync_task.ps1",
        "Asia/Shanghai", "flock", "logs/source-sync.log", "公开展示",
    ):
        assert phrase in readme
