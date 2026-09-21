import datetime as dt
import http.cookiejar
import http.client
import io
import json
import os
import pathlib
import plistlib
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

import lottery_monitor as lm


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_outbound_user_agent_uses_release_version():
    assert lm.USER_AGENT.startswith(f"chusennote/{lm.APP_VERSION} ")


def test_cli_version_uses_release_identity(capsys):
    with pytest.raises(SystemExit) as exit_info:
        lm.parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"lottery_monitor.py {lm.APP_VERSION} ({lm.APP_BUILD})\n"


def test_release_signing_keys_are_excluded_from_git_and_docker_contexts():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()

    for pattern in ("*.keystore", "*.jks", "*.p12", "*.mobileprovision", "*.provisionprofile"):
        assert pattern in gitignore
        assert pattern in dockerignore
        assert f"**/{pattern}" in dockerignore
    assert ".env*" in gitignore
    assert "!.env.example" in gitignore
    assert "**/.env*" in dockerignore
    assert "**/__pycache__/" in dockerignore
    assert "**/*.pyc" in dockerignore


def test_deployment_smoke_checks_every_read_surface(tmp_path):
    server = lm.create_web_server(str(tmp_path / "smoke.sqlite3"), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = lm.smoke_deployment(f"http://127.0.0.1:{server.server_port}")
        postgres_required = lm.smoke_deployment(
            f"http://127.0.0.1:{server.server_port}", require_postgres=True
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["ok"]
    assert result["errors"] == []
    assert len(result["checks"]) == len(lm.SMOKE_ENDPOINTS)
    assert all(check["ok"] for check in result["checks"])
    assert not postgres_required["ok"]
    assert postgres_required["errors"] == ["health: expected PostgreSQL backend"]
    assert len(postgres_required["checks"]) == len(lm.SMOKE_ENDPOINTS)


def test_deployment_smoke_rejects_invalid_base_url_without_requests():
    result = lm.smoke_deployment("file:///tmp/chusennote")

    assert not result["ok"]
    assert result["base_url"] is None
    assert result["checks"] == []
    assert result["errors"] == ["base URL must be credential-free http or https without a query or fragment"]


def test_deployment_smoke_does_not_echo_embedded_credentials():
    result = lm.smoke_deployment("https://user:password@example.com/")

    assert not result["ok"]
    assert "user" not in json.dumps(result)
    assert "password" not in json.dumps(result)


def test_deployment_smoke_rejects_token_over_public_http_without_requests():
    requested = False

    def unexpected_request(request, timeout):
        nonlocal requested
        requested = True
        raise AssertionError("request must not be sent")

    result = lm.smoke_deployment(
        "http://example.com",
        api_token="smoke-secret",
        opener=unexpected_request,
    )

    assert not requested
    assert result == {
        "ok": False,
        "base_url": None,
        "checks": [],
        "errors": [
            "API tokens require HTTPS, localhost, or a literal private-network IP"
        ],
    }
    assert "smoke-secret" not in json.dumps(result)


def test_deployment_smoke_allows_token_for_local_http(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, limit):
            return b"<html><title>chusennote</title></html>"

    requests = []
    monkeypatch.setattr(lm.smoke, "SMOKE_ENDPOINTS", (("home", "/", "html"),))
    result = lm.smoke.smoke_deployment(
        "http://127.0.0.1:8877",
        api_token="local-token",
        opener=lambda request, timeout: requests.append(request) or FakeResponse(),
    )

    assert result["ok"]
    assert requests[0].get_header("Authorization") == "Bearer local-token"


def test_smoke_cli_validates_positive_timeout():
    for value in ("0", "nan", "inf"):
        with pytest.raises(SystemExit):
            lm.parse_args(["smoke", "--timeout", value])
    direct = lm.smoke_deployment("https://example.com", timeout=float("nan"))
    assert direct["errors"] == ["timeout must be finite and greater than zero"]


def test_deployment_smoke_rejects_oversized_and_wrong_shape_responses(monkeypatch):
    class FakeResponse:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, limit):
            return self.payload

    monkeypatch.setattr(lm.smoke, "SMOKE_ENDPOINTS", (("home", "/", "html"),))
    oversized = lm.smoke.smoke_deployment(
        "https://example.com",
        opener=lambda request, timeout: FakeResponse(b"x" * (lm.SMOKE_RESPONSE_LIMIT + 1)),
    )
    assert not oversized["ok"]
    assert oversized["errors"] == ["home: response exceeds 1 MB"]

    monkeypatch.setattr(lm.smoke, "SMOKE_ENDPOINTS", (("events", "/api/events", "list"),))
    wrong_shape = lm.smoke.smoke_deployment(
        "https://example.com",
        opener=lambda request, timeout: FakeResponse(b"{}"),
    )
    assert not wrong_shape["ok"]
    assert wrong_shape["errors"] == ["events: expected a JSON list"]


def test_deployment_smoke_rejects_another_application_release(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, limit):
            return json.dumps(
                {
                    "status": "ok",
                    "version": "9.9.9",
                    "build": 999,
                    "schema_version": lm.DB_SCHEMA_VERSION,
                }
            ).encode()

    monkeypatch.setattr(lm.smoke, "SMOKE_ENDPOINTS", (("health", "/api/health", "health"),))
    result = lm.smoke.smoke_deployment(
        "https://example.com",
        opener=lambda request, timeout: FakeResponse(),
    )

    assert not result["ok"]
    assert result["errors"] == [
        f"health: release metadata does not match {lm.APP_VERSION} build {lm.APP_BUILD}"
    ]


def test_deployment_smoke_accepts_required_postgres_health(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, limit):
            return json.dumps(
                {
                    "status": "ok",
                    "version": lm.APP_VERSION,
                    "build": lm.APP_BUILD,
                    "schema_version": lm.DB_SCHEMA_VERSION,
                    "db_path": "postgresql",
                }
            ).encode()

    monkeypatch.setattr(lm.smoke, "SMOKE_ENDPOINTS", (("health", "/api/health", "health"),))
    result = lm.smoke.smoke_deployment(
        "https://example.com",
        opener=lambda request, timeout: FakeResponse(),
        require_postgres=True,
    )

    assert result["ok"]
    assert result["errors"] == []


def test_release_preflight_reports_configured_components_without_secrets(tmp_path):
    for relative_path in (
        "Dockerfile",
        ".dockerignore",
        "render.yaml",
        ".env.example",
        "lottery_monitor.py",
        "requirements.txt",
        "chusennote/models.py",
        "chusennote/notifications.py",
        "chusennote/preflight.py",
        "chusennote/schema.py",
        "chusennote/smoke.py",
        "chusennote/web.py",
        ".github/workflows/android.yml",
        ".github/workflows/docker.yml",
        ".github/workflows/hosted-monitor.yml",
        ".github/workflows/ios.yml",
        ".github/workflows/postgres.yml",
        ".github/workflows/python.yml",
        "ios/Chusennote.xcodeproj/project.pbxproj",
        "ios/Chusennote.xcodeproj/xcshareddata/xcschemes/Chusennote.xcscheme",
        "ios/Chusennote/Assets.xcassets/AppIcon.appiconset/Contents.json",
        "ios/Chusennote/Chusennote.entitlements",
        "ios/Chusennote/Info.plist",
        "android/gradlew",
        "android/app/build.gradle",
        "android/app/src/main/AndroidManifest.xml",
        "android/app/src/main/res/drawable/ic_launcher.xml",
        "scripts/run-chusennote-once.sh",
        "scripts/run-hosted-monitor.py",
        "scripts/check-chusennote.ps1",
        "scripts/start-chusennote.ps1",
        "ios/Chusennote/GoogleService-Info.plist",
        "android/app/google-services.json",
        "release.keystore",
        "adc.json",
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    (tmp_path / "ios/Chusennote/GoogleService-Info.plist").write_bytes(
        plistlib.dumps(
            {
                "BUNDLE_ID": "com.chusennote.mobile",
                "PROJECT_ID": "example-project",
                "GOOGLE_APP_ID": "example-app",
                "GCM_SENDER_ID": "123456",
            }
        )
    )
    (tmp_path / "android/app/google-services.json").write_text(
        json.dumps(
            {
                "project_info": {"project_id": "example-project", "project_number": "123456"},
                "client": [
                    {
                        "client_info": {
                            "mobilesdk_app_id": "example-app",
                            "android_client_info": {"package_name": "com.chusennote.mobile"},
                        }
                    }
                ],
            }
        )
    )
    (tmp_path / "adc.json").write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "example-project",
                "client_email": "service@example.invalid",
                "private_key": "fixture-key",
            }
        )
    )
    (tmp_path / "android/app/build.gradle").write_text(
        f'versionCode {lm.APP_BUILD}\nversionName "{lm.APP_VERSION}"\n'
    )
    (tmp_path / "ios/Chusennote.xcodeproj/project.pbxproj").write_text(
        "buildSettings = {\n"
        f"CURRENT_PROJECT_VERSION = {lm.APP_BUILD};\nMARKETING_VERSION = {lm.APP_VERSION};\n"
        "PRODUCT_BUNDLE_IDENTIFIER = com.chusennote.mobile;\n"
        "DEVELOPMENT_TEAM = ABC1234567;\nAPS_ENVIRONMENT = production;\n"
        "};\nname = Release;\n"
    )
    environment = {
        "CHUSENNOTE_DATABASE_URL": "postgresql://user:secret@db.example/chusennote",
        "CHUSENNOTE_SEARCH_PROVIDER": "brave",
        "CHUSENNOTE_SEARCH_API_KEY": "search-secret",
        "CHUSENNOTE_FIREBASE_PROJECT_ID": "example-project",
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "adc.json"),
        "CHUSENNOTE_ANDROID_KEYSTORE": str(tmp_path / "release.keystore"),
        "CHUSENNOTE_ANDROID_STORE_PASSWORD": "store-secret",
        "CHUSENNOTE_ANDROID_KEY_ALIAS": "key-secret",
        "CHUSENNOTE_ANDROID_KEY_PASSWORD": "password-secret",
        "CHUSENNOTE_SMTP_HOST": "smtp.example.com",
        "CHUSENNOTE_NOTIFY_EMAIL": "local@example.com",
        "CHUSENNOTE_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/secret",
        "CHUSENNOTE_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/secret",
        "CHUSENNOTE_LINE_CHANNEL_ACCESS_TOKEN": "line-secret",
        "CHUSENNOTE_LINE_TARGET_ID": "target-secret",
    }

    status = lm.release_configuration_status(tmp_path, environment)
    rendered = json.dumps(status)

    assert status["release_files_ready"]
    assert status["release_metadata"] == {
        "ready": True,
        "version": lm.APP_VERSION,
        "build": lm.APP_BUILD,
        "detail": "backend, iOS, and Android metadata match",
    }
    assert all(component["configured"] for component in status["components"].values())
    assert status["unconfigured"] == []
    assert status["production_configuration_ready"] is True
    assert status["production_configuration_unconfigured"] == []
    assert status["external_acceptance_verified"] is False
    assert status["release_ready"] is False
    assert status["external_acceptance_required"] == [
        "deployed-service-smoke",
        "ios-physical-device-push",
        "android-physical-device-push",
    ]
    assert "secret" not in rendered


def test_release_preflight_marks_partial_android_signing_incomplete(tmp_path):
    status = lm.release_configuration_status(
        tmp_path,
        {"CHUSENNOTE_ANDROID_KEYSTORE": str(tmp_path / "missing.keystore")},
    )

    assert not status["components"]["android-signing"]["configured"]
    assert status["components"]["android-signing"]["detail"] == "partially configured"


def test_release_preflight_reports_missing_ios_signing_configuration(tmp_path):
    project = tmp_path / "ios/Chusennote.xcodeproj/project.pbxproj"
    project.parent.mkdir(parents=True)
    project.write_text(
        "buildSettings = {\nPRODUCT_BUNDLE_IDENTIFIER = com.chusennote.mobile;\n"
        "APS_ENVIRONMENT = production;\nCODE_SIGN_STYLE = Automatic;\n};\nname = Release;\n"
    )

    status = lm.release_configuration_status(tmp_path, {})

    assert not status["components"]["ios-signing"]["configured"]
    assert status["components"]["ios-signing"]["detail"] == "missing Apple development team"


def test_release_preflight_rejects_mobile_version_drift(tmp_path):
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "ios/Chusennote.xcodeproj").mkdir(parents=True)
    (tmp_path / "android/app/build.gradle").write_text('versionCode 8\nversionName "1.2.4"\n')
    (tmp_path / "ios/Chusennote.xcodeproj/project.pbxproj").write_text(
        "CURRENT_PROJECT_VERSION = 7;\nMARKETING_VERSION = 1.2.3;\n"
    )

    status = lm.release_configuration_status(tmp_path, {})

    assert not status["release_metadata"]["ready"]
    assert not status["release_files_ready"]


def test_release_preflight_rejects_matching_mobile_metadata_from_another_release(tmp_path):
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "ios/Chusennote.xcodeproj").mkdir(parents=True)
    (tmp_path / "android/app/build.gradle").write_text(
        'versionCode 999\nversionName "9.9.9"\n'
    )
    (tmp_path / "ios/Chusennote.xcodeproj/project.pbxproj").write_text(
        "CURRENT_PROJECT_VERSION = 999;\nMARKETING_VERSION = 9.9.9;\n"
    )

    status = lm.release_configuration_status(tmp_path, {})

    assert status["release_metadata"] == {
        "ready": False,
        "version": "9.9.9",
        "build": 999,
        "detail": "backend, iOS, and Android version/build metadata are missing or inconsistent",
    }
    assert not status["release_files_ready"]


def test_release_preflight_rejects_malformed_or_wrong_target_configuration(tmp_path):
    (tmp_path / "ios/Chusennote").mkdir(parents=True)
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "ios/Chusennote/GoogleService-Info.plist").write_bytes(
        plistlib.dumps({"BUNDLE_ID": "com.example.wrong"})
    )
    (tmp_path / "android/app/google-services.json").write_text(
        json.dumps(
            {
                "project_info": {"project_id": "wrong-project"},
                "client": [
                    {"client_info": {"android_client_info": {"package_name": "com.example.wrong"}}}
                ],
            }
        )
    )
    (tmp_path / "adc.json").write_text("not json")

    status = lm.release_configuration_status(
        tmp_path,
        {
            "CHUSENNOTE_DATABASE_URL": "sqlite:///not-production.sqlite3",
            "CHUSENNOTE_FIREBASE_PROJECT_ID": "example-project",
            "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "adc.json"),
        },
    )

    assert not status["components"]["database"]["configured"]
    assert not status["components"]["fcm-backend"]["configured"]
    assert not status["components"]["ios-firebase"]["configured"]
    assert not status["components"]["android-firebase"]["configured"]


def test_release_preflight_rejects_mismatched_mobile_firebase_projects(tmp_path):
    (tmp_path / "ios/Chusennote").mkdir(parents=True)
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "ios/Chusennote/GoogleService-Info.plist").write_bytes(
        plistlib.dumps(
            {
                "BUNDLE_ID": "com.chusennote.mobile",
                "PROJECT_ID": "ios-project",
                "GOOGLE_APP_ID": "ios-app",
                "GCM_SENDER_ID": "111111",
            }
        )
    )
    (tmp_path / "android/app/google-services.json").write_text(
        json.dumps(
            {
                "project_info": {
                    "project_id": "android-project",
                    "project_number": "222222",
                },
                "client": [
                    {
                        "client_info": {
                            "mobilesdk_app_id": "android-app",
                            "android_client_info": {"package_name": "com.chusennote.mobile"},
                        }
                    }
                ],
            }
        )
    )

    status = lm.release_configuration_status(
        tmp_path,
        {"CHUSENNOTE_FIREBASE_PROJECT_ID": "backend-project"},
    )

    assert not status["components"]["ios-firebase"]["configured"]
    assert status["components"]["ios-firebase"]["detail"] == (
        "iOS Firebase client targets another backend Firebase project"
    )
    assert not status["components"]["android-firebase"]["configured"]
    assert status["components"]["android-firebase"]["detail"] == (
        "Android Firebase client targets another backend Firebase project"
    )


def test_release_preflight_rejects_mismatched_mobile_firebase_senders(tmp_path):
    (tmp_path / "ios/Chusennote").mkdir(parents=True)
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "ios/Chusennote/GoogleService-Info.plist").write_bytes(
        plistlib.dumps(
            {
                "BUNDLE_ID": "com.chusennote.mobile",
                "PROJECT_ID": "shared-project",
                "GOOGLE_APP_ID": "ios-app",
                "GCM_SENDER_ID": "111111",
            }
        )
    )
    (tmp_path / "android/app/google-services.json").write_text(
        json.dumps(
            {
                "project_info": {
                    "project_id": "shared-project",
                    "project_number": "222222",
                },
                "client": [
                    {
                        "client_info": {
                            "mobilesdk_app_id": "android-app",
                            "android_client_info": {"package_name": "com.chusennote.mobile"},
                        }
                    }
                ],
            }
        )
    )

    status = lm.release_configuration_status(
        tmp_path,
        {"CHUSENNOTE_FIREBASE_PROJECT_ID": "shared-project"},
    )

    assert not status["components"]["ios-firebase"]["configured"]
    assert not status["components"]["android-firebase"]["configured"]
    assert status["components"]["ios-firebase"]["detail"] == (
        "iOS and Android Firebase clients use different sender projects"
    )
    assert status["components"]["android-firebase"]["detail"] == (
        "iOS and Android Firebase clients use different sender projects"
    )


def test_release_preflight_cli_only_fails_for_explicit_requirements(monkeypatch, capsys):
    monkeypatch.delenv("CHUSENNOTE_DATABASE_URL", raising=False)

    assert lm.run_command(lm.parse_args(["preflight"])) == 0
    assert lm.run_command(lm.parse_args(["preflight", "--require", "database"])) == 1
    assert lm.run_command(lm.parse_args(["preflight", "--production"])) == 1

    output = capsys.readouterr().out
    assert "Local release files: ready." in output
    assert "database: not configured" in output


def test_ios_plist_declares_all_ipad_orientations():
    with (ROOT / "ios" / "Chusennote" / "Info.plist").open("rb") as plist_file:
        info = plistlib.load(plist_file)

    assert set(info["UISupportedInterfaceOrientations~ipad"]) == {
        "UIInterfaceOrientationPortrait",
        "UIInterfaceOrientationPortraitUpsideDown",
        "UIInterfaceOrientationLandscapeLeft",
        "UIInterfaceOrientationLandscapeRight",
    }


def test_windows_task_scheduler_helpers_keep_expected_contract():
    install_script = (ROOT / "scripts" / "install-chusennote-monitor-task.ps1").read_text()
    show_script = (ROOT / "scripts" / "show-chusennote-monitor-task.ps1").read_text()
    uninstall_script = (ROOT / "scripts" / "uninstall-chusennote-monitor-task.ps1").read_text()

    assert "Register-ScheduledTask" in install_script
    assert "New-ScheduledTaskTrigger" in install_script
    assert '"event", "artist"' in install_script
    assert "lottery_monitor.py" in install_script
    assert '"run"' in install_script
    assert "Get-ScheduledTaskInfo" in show_script
    assert "Unregister-ScheduledTask" in uninstall_script


def test_windows_smoke_helper_delegates_to_canonical_checker():
    script = (ROOT / "scripts" / "check-chusennote.ps1").read_text()

    assert '"lottery_monitor.py", "smoke"' in script
    assert '"--base-url", $BaseUrl' in script
    assert '"--timeout", $TimeoutSec' in script
    assert '"--json"' in script
    assert '"--require-postgres"' in script
    assert "exit $SmokeExitCode" in script
    assert "Invoke-RestMethod" not in script
    assert "Invoke-WebRequest" not in script


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX shell execution")
def test_linux_systemd_helpers_render_safe_user_units_without_installing(tmp_path):
    install_script = ROOT / "scripts" / "install-chusennote-systemd.sh"
    show_script = (ROOT / "scripts" / "show-chusennote-systemd.sh").read_text()
    uninstall_script = (ROOT / "scripts" / "uninstall-chusennote-systemd.sh").read_text()
    env_file = tmp_path / "private monitor.env"
    env_file.write_text("CHUSENNOTE_SEARCH_PROVIDER=brave\n")
    db_path = tmp_path / "database folder" / "monitor.sqlite3"
    environment = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config root")}

    rendered = subprocess.run(
        [
            str(install_script),
            "--kind", "artist",
            "--interval-minutes", "45",
            "--db", str(db_path),
            "--python", sys.executable,
            "--env-file", str(env_file),
            "--dry-run",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "Description=chusennote artist watch check" in rendered
    assert "OnUnitActiveSec=45min" in rendered
    assert f'WorkingDirectory="{ROOT}"' in rendered
    assert f'EnvironmentFile=-"{env_file}"' in rendered
    assert f'--db "{db_path}"' in rendered
    assert "NoNewPrivileges=true" in rendered
    assert "ProtectSystem=strict" in rendered
    assert not (tmp_path / "config root").exists()
    assert "journalctl --user -u chusennote-monitor.service" in show_script
    assert "disable --now chusennote-monitor.timer" in uninstall_script

    invalid = subprocess.run(
        [str(install_script), "--interval-minutes", "0", "--dry-run"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert "at least 1 minute" in invalid.stderr


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX shell execution")
def test_macos_launchd_helpers_render_valid_plist_without_installing(tmp_path):
    install_script = ROOT / "scripts" / "install-chusennote-launchd.sh"
    show_script = (ROOT / "scripts" / "show-chusennote-launchd.sh").read_text()
    uninstall_script = (ROOT / "scripts" / "uninstall-chusennote-launchd.sh").read_text()
    env_file = tmp_path / "private & monitor.env"
    env_file.write_text("CHUSENNOTE_SEARCH_PROVIDER=brave\n")
    db_path = tmp_path / "database folder" / "monitor.sqlite3"
    fake_home = tmp_path / "home folder"
    environment = {**os.environ, "HOME": str(fake_home)}

    output = subprocess.run(
        [
            str(install_script),
            "--kind", "artist",
            "--interval-minutes", "30",
            "--db", str(db_path),
            "--python", sys.executable,
            "--env-file", str(env_file),
            "--dry-run",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    plist = plistlib.loads(output.partition("\n")[2].encode("utf-8"))

    assert plist["Label"] == "com.chusennote.monitor"
    assert plist["StartInterval"] == 1800
    assert plist["RunAtLoad"] is True
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["ProgramArguments"] == [
        str(ROOT / "scripts" / "run-chusennote-once.sh"),
        "--kind", "artist",
        "--db", str(db_path),
        "--python", sys.executable,
        "--env-file", str(env_file),
    ]
    assert not fake_home.exists()
    assert 'launchctl print "gui/$(id -u)/$label"' in show_script
    assert 'launchctl bootout "gui/$(id -u)/$label"' in uninstall_script


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX shell execution")
def test_service_runner_loads_env_as_data_without_executing_it(tmp_path):
    runner = ROOT / "scripts" / "run-chusennote-once.sh"
    capture_path = tmp_path / "captured.txt"
    marker_path = tmp_path / "must-not-exist"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$CHUSENNOTE_TEST_VALUE\" \"$DANGEROUS_VALUE\" \"$@\" > \"$CAPTURE_PATH\"\n"
    )
    fake_python.chmod(0o700)
    env_file = tmp_path / "monitor.env"
    env_file.write_text(
        f"CAPTURE_PATH={capture_path}\n"
        "CHUSENNOTE_TEST_VALUE=value with spaces\n"
        f"DANGEROUS_VALUE=$(touch {marker_path})\n"
    )

    subprocess.run(
        [
            str(runner),
            "--kind", "artist",
            "--db", str(tmp_path / "monitor.sqlite3"),
            "--python", str(fake_python),
            "--env-file", str(env_file),
        ],
        cwd=ROOT,
        check=True,
    )

    captured = capture_path.read_text().splitlines()
    assert captured[0] == "value with spaces"
    assert captured[1] == f"$(touch {marker_path})"
    assert captured[2:5] == ["lottery_monitor.py", "artist", "run"]
    assert not marker_path.exists()


def test_extract_ticket_links_from_official_page():
    html = """
    <html><head><title>Example Musical Official</title></head>
    <body>
      <h1>Example Musical 2026</h1>
      <p>公演日 2026年7月10日 会場 Example Hall</p>
      <a href="https://eplus.jp/example-musical/">チケット抽選先行はこちら</a>
      <a href="/news">News</a>
    </body></html>
    """
    page = lm.parse_page("https://official.example/stage", html)

    info = lm.build_event_info("Example Musical", [page])

    assert info.title == "Example Musical Official"
    assert info.official_page == "https://official.example/stage"
    assert info.ticket_links[0].url == "https://eplus.jp/example-musical/"
    assert "公演日" in info.event_dates[0]
    assert "Example Hall" in info.venues[0]


def test_current_toho_naviserve_domain_is_an_actionable_primary_ticket_link():
    url = "https://tohostage.toho-navi.com/naviserve/pt/"

    assert lm.source_name_for_url(url) == "toho-navi"
    assert lm.source_provenance(url, "東宝ナビザーブ") == "ticket_primary"
    assert lm.is_actionable_ticket_link(url, "東宝ナビザーブ") is True


def test_parse_page_retains_nonempty_image_alt_as_public_evidence():
    page = lm.parse_page(
        "https://official.example/stage",
        """
        <html><body>
          <img src="schedule.svg" alt="公演期間：2026年8月9日(日)～9月13日(日)">
          <a href="/venue"><img src="venue.svg" alt="東京建物 Brillia HALL"></a>
          <img src="decoration.svg" alt="">
        </body></html>
        """,
    )

    assert "公演期間：2026年8月9日(日)～9月13日(日)" in page.text
    assert "東京建物 Brillia HALL" in page.text
    assert page.links[0].label == "東京建物 Brillia HALL"


def test_parse_page_and_xml_support_declared_feed_and_sitemap_discovery():
    page = lm.parse_page(
        "https://official.example/stage",
        """
        <html><head>
          <link rel="alternate" type="application/rss+xml" href="/news/feed.xml">
          <link rel="sitemap" href="/sitemap.xml">
        </head><body>Example Stage</body></html>
        """,
    )
    assert [link.url for link in page.discovery_links] == [
        "https://official.example/news/feed.xml",
        "https://official.example/sitemap.xml",
    ]

    sitemap_pages, sitemap_manifests = lm.parse_discovery_document(
        "https://official.example/sitemap.xml",
        """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>/events.xml</loc></sitemap></sitemapindex>""",
    )
    assert sitemap_pages == ()
    assert sitemap_manifests == ("https://official.example/events.xml",)
    event_pages, nested = lm.parse_discovery_document(
        sitemap_manifests[0],
        """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://official.example/news/example-stage</loc></url></urlset>""",
    )
    assert nested == ()
    assert event_pages[0].url == "https://official.example/news/example-stage"
    atom_pages, _ = lm.parse_discovery_document(
        "https://official.example/feed.atom",
        """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Example Stage</title>
        <link rel="self" href="/feed.atom"/><link rel="alternate" href="/news/example-stage"/>
        </entry></feed>""",
    )
    assert atom_pages[0].url == "https://official.example/news/example-stage"


def test_official_event_json_ld_enriches_event_info_and_ticket_link():
    html = """
    <html><head>
      <title>Generic page title</title>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@graph": [
          {"@type": "WebSite", "name": "Example Site"},
          {
            "@type": "MusicEvent",
            "name": "Example Live 2026",
            "description": "One-night official performance.",
            "startDate": "2026-10-24T18:00:00+09:00",
            "endDate": "2026-10-24T21:00:00+09:00",
            "location": {"@type": "Place", "name": "Example Arena"},
            "organizer": {"@type": "Organization", "name": "Example Productions"},
            "performer": [
              {"@type": "MusicGroup", "name": "Example Band"},
              {"@type": "Person", "name": "Example Guest"}
            ],
            "offers": {
              "@type": "Offer",
              "name": "Official tickets",
              "url": "https://eplus.jp/example-live/"
            }
          }
        ]
      }
      </script>
    </head><body><p>Event details</p></body></html>
    """
    page = lm.parse_page("https://official.example/live", html)

    info = lm.build_event_info("Example Live", [page])

    assert len(page.structured_data) == 1
    assert lm.page_matches_keyword("Example Live", page) is True
    assert info.title == "Example Live 2026"
    assert info.summary == "One-night official performance."
    assert info.event_dates == ("2026-10-24T18:00:00+09:00 – 2026-10-24T21:00:00+09:00",)
    assert info.venues == ("Example Arena",)
    assert info.organizers == ("Example Productions",)
    assert info.lineup == ("Example Band", "Example Guest")
    assert info.ticket_links == (lm.Link("Official tickets", "https://eplus.jp/example-live/"),)


def test_invalid_json_ld_is_ignored_without_losing_visible_page_content():
    page = lm.parse_page(
        "https://official.example/live",
        '<html><head><title>Visible title</title><script type="application/ld+json">{bad</script></head>'
        '<body><p>公演日 2026年7月10日 会場 Example Hall</p></body></html>',
    )

    info = lm.build_event_info("Example", [page])

    assert page.structured_data == ()
    assert info.title == "Visible title"
    assert "公演日" in info.event_dates[0]


def test_extract_ticket_links_ignores_social_share_and_info_urls():
    html = """
    <html><body>
      <a href="http://twitter.com/share?text=チケット">Xで投稿する</a>
      <a href="http://line.me/R/msg/text/?チケット">LINEで送る</a>
      <a href="https://www.shiki.jp/applause/lionking/ticket_schedule/">チケット＆スケジュール</a>
    </body></html>
    """
    page = lm.parse_page("https://www.shiki.jp/applause/lionking/", html)

    links = lm.extract_ticket_links(page)

    assert links == ()


def test_extract_ticket_links_ignores_ticket_related_info_pages():
    html = """
    <html><body>
      <a href="https://horipro-stage.jp/stage/example/#schedule">Tickets &amp; Schedule</a>
      <a href="https://www.nissay-plus.co.jp/horipro-ticket-hoken?ch=abc">Ticket insurance</a>
      <a href="https://w.pia.jp/t/example/">Pia lottery</a>
      <a href="https://ticket.tv-asahi.co.jp/ex/project/example">TV Asahi ticket</a>
    </body></html>
    """
    page = lm.parse_page("https://horipro-stage.jp/stage/example/", html)

    links = lm.extract_ticket_links(page)

    assert [link.url for link in links] == [
        "https://w.pia.jp/t/example/",
        "https://ticket.tv-asahi.co.jp/ex/project/example",
    ]


def test_event_status_ignores_non_actionable_ticket_info_links():
    info = lm.EventInfo(
        keyword="Example",
        official_page="https://official.example/stage",
        title="Example",
        summary="",
        event_dates=(),
        venues=(),
        ticket_links=(lm.Link("Tickets & Schedule", "https://horipro-stage.jp/stage/example/#schedule"),),
    )

    assert lm.compute_event_status(info, ()) == "official_found"


def test_search_api_warns_when_configured_backend_fails(monkeypatch, capsys):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "brave")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "secret-key")

    def fail_request_json(url, headers=None):
        raise OSError("bad credentials")

    monkeypatch.setattr(lm, "request_json", fail_request_json)

    assert lm.search_api("Example") == []

    warning = capsys.readouterr().err
    assert "Warning:" in warning
    assert "CHUSENNOTE_SEARCH_PROVIDER='brave' failed" in warning
    assert "secret-key" not in warning


def test_search_api_warns_for_unsupported_provider(monkeypatch, capsys):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "unknown")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "secret-key")

    assert lm.search_api("Example") == []

    warning = capsys.readouterr().err
    assert "unsupported CHUSENNOTE_SEARCH_PROVIDER='unknown'" in warning
    assert "secret-key" not in warning


def test_event_dates_and_venues_ignore_ticket_sales_noise():
    text = """
    SCHEDULE & TICKETS スケジュール＆チケット 群馬公演 【一般前売開始】 2025年4月5日(土)
    ※車椅子席、介助席のご購入は、高崎芸術劇場チケットセンター（027-321-3900）まで電話でお問合せください。（6月10日追記）
    【料金】 S席 17,500円 A席 11,000円
    東宝ナビザーブ 先行抽選エントリー 3月18日(火)～3月21日(金)まで
    会場のご案内 高崎芸術劇場 大劇場 〒370-7302 群馬県高崎市栄町9-1 MAP 座席表
    """

    assert lm.extract_event_dates(text) == ()
    assert lm.extract_venues(text)[0] == "高崎芸術劇場 大劇場"


def test_event_dates_ignore_performance_day_deadline_prose():
    text = (
        "当日券は事前販売いたします。各公演日の前日18:00から受付いたします。 "
        "News 2026.05.08 新作ミュージカルのお知らせ"
    )

    assert lm.extract_event_dates(text) == ()


def test_extract_venues_stops_at_organizer_and_contact_noise():
    text = (
        "出演（柿澤勇人、石井一孝） 会場 梅田芸術劇場メインホール 主催 梅田芸術劇場 "
        "お問い合わせ 梅田芸術劇場 0570-077-0 ぜひ劇場でお楽しみください"
    )

    assert lm.extract_venues(text) == ("梅田芸術劇場メインホール",)


def test_extract_event_organizers_and_lineup_from_explicit_labels():
    text = (
        "公演概要 主催：梅田芸術劇場／関西テレビ "
        "企画・制作：ホリプロ 出演：山田太郎、佐藤花子 会場：例ホール"
    )

    assert lm.extract_organizers(text) == ("梅田芸術劇場", "関西テレビ", "ホリプロ")
    assert lm.extract_lineup(text) == ("山田太郎", "佐藤花子")


def test_extract_event_facts_from_colonless_stage_page_sections():
    text = (
        "舞台監督 加藤 高 主催・企画制作 ホリプロ Tickets & Schedule "
        "Cast 海宝直人 （トラック1）：ニコラ・テスラ コメント "
        "成河 （トラック3）：父／教授 コメント 濱田めぐみ （トラック2）：母 コメント "
        "昆 夏美 （トラック4）：ナース Staff 音楽・歌詞 ニック・ブッチャー "
        "Tour 大阪公演 会場 梅田芸術劇場 主催 梅田芸術劇場 お問い合わせ 0570-000-000"
    )

    assert lm.extract_organizers(text) == ("ホリプロ", "梅田芸術劇場")
    assert lm.extract_lineup(text) == ("海宝直人", "成河", "濱田めぐみ", "昆 夏美")


def test_event_fact_extraction_requires_labels_and_avoids_summary_names():
    text = (
        "山田太郎と佐藤花子が梅田芸術劇場の新作を紹介します。 "
        "本公演はホリプロが主催 する予定です。"
    )

    assert lm.extract_organizers(text) == ()
    assert lm.extract_lineup(text) == ()


def test_build_event_info_merges_organizer_and_lineup_facts_across_pages():
    pages = (
        lm.Page(
            "https://official.example/overview",
            "Example Event",
            "主催：Example Productions 出演：Example Lead、Example Guest 会場：Example Hall",
            (),
        ),
        lm.Page(
            "https://official.example/cast",
            "Cast",
            "企画・制作：Example Productions CAST: Example Guest / Example Ensemble",
            (),
        ),
    )

    info = lm.build_event_info("Example", pages)

    assert info.organizers == ("Example Productions",)
    assert info.lineup == ("Example Lead", "Example Guest", "Example Ensemble")


def test_extract_venues_handles_spaced_label_and_suffixless_venue():
    text = "出演（柿澤勇人） 会 場 EXシアター有明(東京ドリームパーク内) 座席表 上演時間 3時間"

    assert lm.extract_venues(text) == ("EXシアター有明(東京ドリームパーク内)",)


def test_extract_event_dates_captures_performance_period_not_ticketing():
    text = (
        "ミュージカル『例』 期 間 2026年7月25日(土)～8月23日(日) 会 場 例ホール "
        "【抽選先行】 受付 期間 2026年1月24日(土)～2月1日(日)"
    )

    dates = lm.extract_event_dates(text)

    assert "2026年7月25日(土)～8月23日(日)" in dates
    assert all("2026年1月24日" not in date for date in dates)


def test_extract_event_dates_captures_shiki_slash_performance_periods():
    text = "劇団四季自動予約 2026/1/2～2026/6/30 公演 No. 3016 2026/7/1～2026/12/31 公演 No. 6118"

    assert lm.extract_event_dates(text) == ("2026/1/2～2026/6/30", "2026/7/1～2026/12/31")


def test_extract_event_dates_canonicalizes_labeled_slash_range_with_page_year():
    text = "奥華子 CONCERT TOUR 2026 公演日： 11/26 ～ 11/26 会場：めぐろパーシモンホール 大ホール"

    assert lm.extract_event_dates(text) == ("2026/11/26～2026/11/26",)


def test_extract_venues_ignores_schedule_labels_and_captures_uppercase_dome():
    text = "2026 CONCERT Special Edition in TOKYO DOME 会場 開場 16:00 開演 18:00"

    assert lm.extract_venues(text) == ("TOKYO DOME",)


def test_toho_image_and_tour_evidence_produces_dates_venues_and_lineup():
    text = (
        "CAST 信 しん 三浦宏規 龐煖 ほうけん 東 啓介 COMMENT "
        "河了貂 かりょうてん 華 優希 COMMENT CREATIVES TICKETS & SCHEDULE "
        "公演期間：2026年8月9日(日)～9月13日(日) 東京建物 Brillia HALL "
        "会場のご案内 〒170-0013 東京都豊島区東池袋1-19-1 座席表 "
        "TOUR 大阪公演 9月21日(月)～29日(火) 新歌舞伎座 詳細 "
        "福岡公演 10月6日(火)～13日(火) 博多座 詳細"
    )

    assert lm.extract_event_dates(text) == (
        "2026年8月9日(日)～9月13日(日)",
        "大阪公演 2026年9月21日(月)～29日(火)",
        "福岡公演 2026年10月6日(火)～13日(火)",
    )
    assert lm.extract_venues(text) == ("新歌舞伎座", "博多座", "東京建物 Brillia HALL")
    assert lm.extract_lineup(text) == ("東 啓介", "華 優希")


def test_toho_lottery_prose_does_not_borrow_later_general_sale_date():
    page = lm.Page(
        "https://www.tohostage.com/example/",
        "Ticket",
        (
            "2026年 先行抽選エントリーおよび先行先着販売はプレミアム会員限定です。 "
            "先行抽選エントリー 6月9日(火)～6月15日(月) "
            "一般前売日より購入できます。 豊島区民先行抽選エントリー "
            "6月19日(金)～6月24日(水) 一般前売 7月13日(月)販売開始"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds(page)
    lottery_rounds = [round_ for round_ in rounds if "抽選" in round_.name]

    assert [(round_.lottery_start, round_.lottery_end) for round_ in lottery_rounds] == [
        ("2026-06-09", "2026-06-15"),
    ]
    assert all(round_.lottery_start or round_.lottery_end for round_ in lottery_rounds)


def test_extract_venues_prefers_concise_shiki_theater_name():
    text = "アラジン 東京 電通四季劇場［海］（汐留） 選択 北海道 青森 劇場アクセス 作品紹介"

    assert lm.extract_venues(text)[0] == "電通四季劇場［海］（汐留）"


def test_extract_venues_ignores_shiki_no_schedule_notice():
    text = (
        "チケット購入はできません。 ＞「有明四季劇場」交通アクセス・駐車場のご案内 "
        "現在、公演スケジュール情報はありません。 公演一覧はこちら Facebookでシェアする LINEで送る"
    )

    assert lm.extract_venues(text) == ()


def test_infer_event_location_prefers_parenthetical_area():
    assert lm.infer_event_location(("Venue Example Hall (Tokyo)",)) == "Tokyo"


def test_ticket_rule_and_price_extractors_read_summary_notes():
    summary = (
        "Schedule ※未就学児のご入場はご遠慮ください。"
        "※本公演のチケットは主催者の同意のない有償譲渡が禁止されています。"
        "【料金】 S席 17,500円 A席 11,000円"
    )

    assert "未就学児" in lm.extract_ticket_rule_items(summary)[0]
    assert "有償譲渡" in lm.extract_ticket_rule_items(summary)[1]
    assert lm.extract_ticket_price_items(summary) == ("S席 17,500円 A席 11,000円",)


def test_ticket_price_extractor_splits_structured_seat_tiers():
    summary = "チケット S席：平日14,000円／土日祝15,000円 A席：平日9,000円／土日祝10,000円 Yシート（20歳以下当日引換券）：2,000円＊ U-25（25歳以下当日引換券）：5,500円"

    assert lm.extract_ticket_price_items(summary) == (
        "S席：平日14,000円／土日祝15,000円",
        "A席：平日9,000円／土日祝10,000円",
        "Yシート（20歳以下当日引換券）：2,000円",
        "U-25（25歳以下当日引換券）：5,500円",
    )


def test_ticket_rule_extractor_merges_continuations_and_skips_notice_links():
    summary = (
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください。"
        "なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "※【重要なお知らせ】高額転売チケットに関する注意喚起 ＞＞"
    )

    assert lm.extract_ticket_rule_items(summary) == (
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください なお、車椅子スペースをご利用の場合は、S席をご購入ください",
    )


def test_ticket_rule_extractor_dedupes_contained_notes_and_skips_cookie_consent():
    summary = (
        "※車椅子でご来場のお客様は、ご観劇日の1週間前までにホリプロチケットセンターまでご連絡ください。"
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください。"
        "なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "サイトを閲覧いただく際には、クッキーの使用に同意いただく必要があります。"
    )

    rules = lm.extract_ticket_rule_items(summary)

    assert len(rules) == 2
    assert sum("車椅子スペース" in rule for rule in rules) == 1
    assert all("クッキー" not in rule for rule in rules)


def test_ticket_rule_extractor_removes_embedded_ticket_urls():
    rules = lm.extract_ticket_rule_items(
        "※未就学児入場不可 https://l-tike.com/example/ ※転売は禁止です //eplus.jp/example/ 同意"
    )

    assert rules == ("※未就学児入場不可", "※転売は禁止です")


def test_format_evidence_snippet_removes_notice_links_and_truncates():
    evidence = "noise before label ※【重要なお知らせ】高額転売チケットに関する注意喚起 ＞＞ 【抽選先行】 2026年1月24日(土)12:00～2月1日(日)23:59 https://example.com/source " + ("details " * 40)

    snippet = lm.format_evidence_snippet(evidence, limit=80)

    assert "重要なお知らせ" not in snippet
    assert "https://" not in snippet
    assert snippet.startswith("【抽選先行】")
    assert snippet.endswith("...")
    assert len(snippet) <= 83


def test_build_event_info_separates_ticket_prices_and_rules_from_summary():
    page = lm.parse_page(
        "https://official.example/stage",
        """
        <html><head><title>Example Stage</title></head><body>
          <p>公演日 2026年7月10日 会場 Example Hall</p>
          <p>チケット S席：平日14,000円／土日祝15,000円 A席：9,000円</p>
          <p>※未就学児のご入場はご遠慮ください。</p>
        </body></html>
        """,
    )

    info = lm.build_event_info("Example", [page])

    assert info.ticket_prices == ("S席：平日14,000円／土日祝15,000円", "A席：9,000円")
    assert info.ticket_rules == ("※未就学児のご入場はご遠慮ください",)
    assert "14,000円" not in (info.summary or "")
    assert "未就学児" not in (info.summary or "")


def test_extract_ticket_rounds_with_japanese_lottery_dates():
    html = """
    <html><head><title>Ticket</title></head><body>
      <section>
        <h2>第1次抽選先行</h2>
        <p>受付期間 2026年6月10日(水) 12:00 ～ 2026年6月18日(木) 23:59</p>
        <p>抽選結果発表 2026年6月22日(月)</p>
        <p>入金期間 2026年6月22日(月) ～ 2026年6月25日(木)</p>
      </section>
      <section>
        <h2>一般発売</h2>
        <p>発売日 2026/07/04 10:00</p>
      </section>
    </body></html>
    """
    page = lm.parse_page("https://t.pia.jp/pia/event/example", html)

    rounds = lm.extract_ticket_rounds(page)

    assert rounds[0].name == "第1次抽選先行"
    assert rounds[0].lottery_start == "2026-06-10"
    assert rounds[0].lottery_end == "2026-06-18"
    assert rounds[0].results_date == "2026-06-22"
    assert rounds[0].payment_deadline == "2026-06-25"
    assert rounds[0].payment_start_at == "2026-06-22"
    assert rounds[0].payment_end_at == "2026-06-25"
    assert any(round_.general_sale_date == "2026-07-04" for round_ in rounds)


def test_bare_dates_inherit_year_stated_on_page():
    # A page that states its year once up top, then prints round/sale dates as
    # bare M月D日 (tohostage's shape). Those bare dates must resolve to 2025 (the
    # year the page prints), not a today-relative guess. The presale block sits
    # below a rules block — far from the year heading — so it relies on the
    # page-level year fallback, exactly as on the real site. Applies everywhere.
    filler = "※未就学児のご入場はご遠慮いただいております。※公演情報などに変更が生じる場合がございます。" * 4
    html = f"""
    <html><body>
      <p>【一般前売開始】 2025年4月5日(土)</p>
      <p>{filler}</p>
      <p>先行抽選エントリー 3月18日(火)～3月21日(金)まで</p>
      <p>一般前売 4月5日(土) 11:00販売開始</p>
    </body></html>
    """
    page = lm.parse_page("https://www.tohostage.com/example/ticket.html", html)
    rounds = lm.extract_ticket_rounds(page)
    presale = next(round_ for round_ in rounds if "抽選" in round_.name)
    assert presale.lottery_start == "2025-03-18"
    assert presale.lottery_end == "2025-03-21"
    assert presale.general_sale_date == "2025-04-05"


def test_dominant_year_only_resolves_unambiguous_pages():
    assert lm.dominant_year("公演 2026年7月10日 受付 7月1日") == 2026
    # A page mixing years stays ambiguous and keeps today-relative inference.
    assert lm.dominant_year("Copyright 2024 公演 2026年7月10日") is None
    assert lm.dominant_year("先行 3月18日(火)") is None


def test_extract_ticket_rounds_reads_member_presale_ranges_from_evidence():
    text = (
        "2026年公演 ホリプロステージで購入 【先着先行】 "
        "ゴールド会員：2月28日(土)12:00～3月15日(日)23:59 "
        "レギュラー会員：2月28日(土)13:00～3月15日(日)23:59 "
        "【一般発売】 3月18日(水)11:00～"
    )
    page = lm.Page("https://horipro-stage.jp/stage/example/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert {
        (round_.name, round_.lottery_start, round_.lottery_end)
        for round_ in rounds
        if "会員" in round_.name
    } == {
        ("先着先行 / ゴールド会員", "2026-02-28", "2026-03-15"),
        ("先着先行 / レギュラー会員", "2026-02-28", "2026-03-15"),
    }
    assert all(
        round_.lottery_start is None and round_.lottery_end is None
        for round_ in rounds
        if round_.name == "一般発売"
    )


def test_extract_ticket_rounds_keeps_adjacent_round_dates_separate():
    text = (
        "ホリプロステージで購入 【抽選先行】 "
        "2026年1月24日(土)12:00～2月1日(日)23:59 "
        "【先着先行】 "
        "ゴールド会員：2月28日(土)12:00～3月15日(日)23:59 "
        "レギュラー会員：2月28日(土)13:00～3月15日(日)23:59 "
        "【一般発売】 3月18日(水)11:00～"
    )
    page = lm.Page("https://horipro-stage.jp/stage/example/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)
    lottery = next(round_ for round_ in rounds if round_.name == "抽選先行")
    member_rounds = [round_ for round_ in rounds if round_.name.startswith("先着先行 /")]

    assert (lottery.lottery_start, lottery.lottery_end) == ("2026-01-24", "2026-02-01")
    assert "先着先行" not in lottery.evidence
    assert {
        (round_.name, round_.lottery_start, round_.lottery_end)
        for round_ in member_rounds
    } == {
        ("先着先行 / ゴールド会員", "2026-02-28", "2026-03-15"),
        ("先着先行 / レギュラー会員", "2026-02-28", "2026-03-15"),
    }


def test_extract_ticket_rounds_reads_shiki_dates_before_labels():
    text = (
        "2026年 東京公演はこちら 1月10日（火）～8月26日（土）長期保守点検 "
        "8月27日（日）～12月31日（日）公演分 "
        "2月19日（日）「四季の会」会員先行予約／2月26日（日）一般発売開始"
    )
    page = lm.Page("https://www.shiki.jp/applause/aladdin/ticket_schedule/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert rounds[0].lottery_start == "2026-02-19"
    assert rounds[0].lottery_end is None
    assert rounds[0].general_sale_date == "2026-02-26"


def test_past_general_sale_round_is_closed():
    ticket = lm.TicketRound(source="official", url="https://example.test", name="一般発売", general_sale_date="2026-02-26")

    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 6, 13)) == "closed"


def test_extract_ticket_rounds_creates_standalone_official_resale_round():
    page = lm.parse_page(
        "https://official.example/tickets",
        """
        <html><body>
          <h2>公式リセールのお知らせ</h2>
          <p>公式トレード期間：2026年7月1日 ～ 2026年7月3日</p>
        </body></html>
        """,
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "公式トレード"
    assert rounds[0].round_type == "trade"
    assert rounds[0].trade_start_at == "2026-07-01"
    assert rounds[0].trade_end_at == "2026-07-03"


@pytest.mark.parametrize(
    ("label", "expected_name"),
    (
        ("リセール受付期間", "リセール"),
        ("リセール申込期間", "リセール"),
        ("定価リセール受付期間", "定価リセール"),
        ("公式リセール期間", "公式リセール"),
        ("トレード受付期間", "トレード"),
    ),
)
def test_extract_ticket_rounds_accepts_official_resale_label_variants(label, expected_name):
    page = lm.Page(
        "https://official.example/resale",
        "Resale",
        f"{label}：2026年8月10日 10:00 ～ 2026年8月12日 23:59",
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == expected_name
    assert (rounds[0].trade_start_at, rounds[0].trade_end_at) == ("2026-08-10", "2026-08-12")


def test_official_resale_round_status_tracks_open_and_closing_window():
    ticket = lm.TicketRound(
        source="official",
        url="https://official.example/tickets",
        name="公式トレード",
        trade_start_at="2026-07-01",
        trade_end_at="2026-07-05",
    )

    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 6, 30)) == "upcoming"
    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 7, 1)) == "trade_open"
    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 7, 3)) == "trade_closing_soon"
    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 7, 6)) == "closed"


def test_ticket_round_key_distinguishes_same_name_rounds_by_dates():
    first = lm.TicketRound(source="official", url="https://example.test", name="抽選", lottery_start="2026-01-24")
    second = lm.TicketRound(source="official", url="https://example.test", name="抽選", lottery_start="2026-03-24")

    assert lm.ticket_round_key(first) != lm.ticket_round_key(second)

    first_trade = lm.TicketRound(
        source="official", url="https://example.test", name="公式トレード", trade_start_at="2026-07-01"
    )
    second_trade = lm.TicketRound(
        source="official", url="https://example.test", name="公式トレード", trade_start_at="2026-08-01"
    )
    assert lm.ticket_round_key(first_trade) != lm.ticket_round_key(second_trade)


def test_extract_ticket_rounds_reads_toho_advance_sale_labels():
    text = (
        "2026年 東宝ナビザーブ 先行抽選エントリー 3月18日(火)～3月21日(金)まで "
        "先行先着販売 3月30日(日)11:00より販売開始 "
        "一般前売 4月5日(土) 11:00販売開始"
    )
    page = lm.Page("https://www.tohostage.com/lesmiserables/ticket_gunma.html", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert any(round_.lottery_start == "2026-03-18" and round_.lottery_end == "2026-03-21" for round_ in rounds)
    assert any(round_.lottery_start == "2026-03-30" for round_ in rounds)
    assert any(round_.general_sale_date == "2026-04-05" for round_ in rounds)


def test_toho_adapter_preserves_branded_advance_round_name():
    page = lm.Page(
        "https://www.tohostage.com/example/ticket.html",
        "Ticket",
        (
            "2026年 東宝ナビザーブ 先行抽選エントリー "
            "3月18日(火)10:00～3月21日(金)23:59 結果発表 3月25日(水)"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "東宝ナビザーブ 先行抽選エントリー"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-03-18", "2026-03-21")
    assert rounds[0].results_date == "2026-03-25"


def test_horipro_adapter_preserves_fastest_round_and_result_date():
    page = lm.Page(
        "https://horipro-stage.jp/stage/example/",
        "Ticket",
        "2026年【最速抽選先行】5月2日(土)10:00～5月10日(日)23:59 結果発表日：5月14日(木)",
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "最速抽選先行"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-05-02", "2026-05-10")
    assert rounds[0].results_date == "2026-05-14"


def test_shiki_adapter_reads_dates_before_quoted_membership_and_general_sale_labels():
    page = lm.Page(
        "https://www.shiki.jp/applause/example/ticket_schedule/",
        "Ticket",
        (
            "2026年 発売日程 7月18日（土）午前10時 「四季の会」会員先行予約開始 "
            "7月25日（土）午前10時 一般発売開始"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)
    member = next(round_ for round_ in rounds if "会員先行予約" in round_.name)

    assert member.name == "「四季の会」会員先行予約"
    assert member.application_start_at == "2026-07-18"
    assert member.general_sale_date == "2026-07-25"
    assert member.membership_required == "yes"


def test_round_name_uses_label_governing_the_date_not_global_priority():
    # A single window holds two rounds: the 3/18 dates belong to 抽選先行 and the
    # 3/30 dates to 先着先行. The name must follow the nearest preceding label
    # rather than a fixed priority that would tag both as 先着先行.
    text = (
        "ホリプロステージで購入 【抽選先行】 "
        "2026年2月28日(土)12:00～3月15日(日)23:59 "
        "【先着先行】 3月20日(金)11:00～3月25日(水)23:59 "
        "【一般発売】 3月18日(水)11:00～"
    )
    page = lm.Page("https://horipro-stage.jp/stage/example/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)
    by_dates = {(round_.lottery_start, round_.lottery_end): round_.name for round_ in rounds}

    assert by_dates.get(("2026-02-28", "2026-03-15")) == "抽選先行"
    assert by_dates.get(("2026-03-20", "2026-03-25")) == "先着先行"


def test_streaming_and_profile_domains_are_filtered_as_noisy():
    for url in (
        "https://music.apple.com/us/artist/yoasobi/1490256993",
        "https://jpop.fandom.com/wiki/YOASOBI",
        "https://kprofiles.com/yoasobi-members-profile/",
        "https://www.bilibili.tv/en/video/4793901433229824",
    ):
        assert lm.is_noisy_url(url)


def test_extract_tour_dates_reads_date_and_venue():
    text = "LIVE 2026年7月25日(土) 東京 有明アリーナ 2026年8月12日(水) 大阪 大阪城ホール"
    page = lm.parse_page("https://artist.example/live/", f"<html><body>{text}</body></html>")

    entries = lm.extract_tour_dates(page)

    assert {(entry["date"], entry["venue"]) for entry in entries} == {
        ("2026-07-25", "東京 有明アリーナ"),
        ("2026-08-12", "大阪 大阪城ホール"),
    }


def test_tour_venue_reads_place_named_in_title():
    # International tours name the place inside the title; capture it. Multi-city
    # tours and festivals name no single venue, so return nothing rather than a
    # false match on tour-descriptor words like DOME/STADIUM.
    assert lm.tour_venue_from_window("YOASOBI LIVE AT WEMBLEY ARENA") == "WEMBLEY ARENA"
    assert lm.tour_venue_from_window("YOASOBI ASIA TOUR 2025 IN JAKARTA") == "JAKARTA"
    assert lm.tour_venue_from_window("YOASOBI ASIA 10-CITY DOME & STADIUM TOUR 2026") == ""
    assert lm.tour_venue_from_window("NORTH AMERICA TOUR 2026 NEVER ENDING STORIES") == ""


def test_artist_venue_label_is_honest_when_no_venue():
    assert lm.web.artist_venue_label({"venues": ["東京 有明アリーナ"], "title": "Show"}) == "東京 有明アリーナ"
    assert lm.web.artist_venue_label({"venues": [], "title": "YOASOBI ASIA 10-CITY DOME & STADIUM TOUR"}) == "Multiple cities"
    assert lm.web.artist_venue_label({"venues": [], "title": "PENTATONIC"}) == "—"


def test_tour_detail_url_matches_show_link():
    page = lm.parse_page(
        "https://artist.example/live",
        "<html><body>"
        "<a href='https://artist.example/show/summer'>SUMMER FESTIVAL 2026</a>"
        "<a href='https://artist.example/news'>NEWS</a>"
        "<a href='https://twitter.com/artist'>twitter</a>"
        "</body></html>",
    )
    # The link whose label names the show wins; nav/social links never match.
    assert lm.tour_detail_url(page, "SUMMER FESTIVAL 2026") == "https://artist.example/show/summer"
    # A multi-city tour with no matching link enriches nothing (safe no-op).
    assert lm.tour_detail_url(page, "ASIA 10-CITY DOME & STADIUM TOUR") is None


def test_venue_from_detail_page_reads_venue(monkeypatch):
    detail = lm.parse_page(
        "https://artist.example/show/summer",
        "<html><body>会場 東京 有明アリーナ 公演日 2026年7月25日</body></html>",
    )
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: detail)
    assert "有明アリーナ" in lm.venue_from_detail_page("https://artist.example/show/summer")


def test_build_artist_event_blocks_fills_venue_from_detail_page(monkeypatch):
    keyword = "Artist"
    results = [lm.SearchResult("Artist Official", "https://artist.example/", keyword)]
    # The landing page lists a show with no inline venue but links to its detail
    # page. "FESTIVAL"/"show" carry no schedule hint, so the link is not chased
    # as a schedule page and stays for venue enrichment.
    landing_html = (
        "<html><head><title>Artist Official</title></head><body>"
        "Artist 2026 ライブ情報 2026年7月25日(土) SUMMER FESTIVAL 2026 "
        "<a href='https://artist.example/show/summer'>SUMMER FESTIVAL 2026</a>"
        "</body></html>"
    )
    detail_html = "<html><body>会場 東京 有明アリーナ 公演日 2026年7月25日</body></html>"

    def fake_fetch(url):
        if "/show/summer" in url:
            return lm.parse_page(url, detail_html)
        return lm.parse_page("https://artist.example/", landing_html)

    monkeypatch.setattr(lm.pipeline, "search_web", lambda kw, limit=8: results)
    monkeypatch.setattr(lm.pipeline, "choose_official_results", lambda res, kw, limit=8: res)
    monkeypatch.setattr(lm.pipeline, "page_matches_keyword", lambda kw, page: True)
    monkeypatch.setattr(lm.pipeline, "fetch_page", fake_fetch)

    blocks = lm.build_artist_event_blocks(keyword)
    venues = [venue for block in blocks for venue in block.general_info.venues]
    assert any("有明アリーナ" in venue for venue in venues)


def test_build_artist_event_blocks_lists_shows_from_schedule(monkeypatch):
    keyword = "YOASOBI"
    results = [lm.SearchResult("YOASOBI Official", "https://www.yoasobi-music.jp/", keyword)]
    schedule_html = (
        "<html><head><title>YOASOBI Official</title></head><body>"
        "YOASOBI LIVE 2026 ライブ情報 "
        "2026年7月25日(土) 東京 有明アリーナ "
        "2026年8月12日(水) 大阪 大阪城ホール"
        "</body></html>"
    )
    monkeypatch.setattr(lm.pipeline, "search_web", lambda kw, limit=8: results)
    monkeypatch.setattr(lm.pipeline, "choose_official_results", lambda res, kw, limit=8: res)
    monkeypatch.setattr(lm.pipeline, "page_matches_keyword", lambda kw, page: True)
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: lm.parse_page("https://www.yoasobi-music.jp/", schedule_html))

    blocks = lm.build_artist_event_blocks(keyword)

    titles = [block.general_info.title for block in blocks]
    assert any("有明アリーナ" in title for title in titles)
    assert any("大阪城ホール" in title for title in titles)
    # Each show is a distinct, clickable event under the artist.
    urls = [block.general_info.official_page for block in blocks]
    assert len(set(urls)) == len(urls)
    assert all(str(url).startswith("https://www.yoasobi-music.jp/#") for url in urls)
    assert all(block.ticket_info == () for block in blocks)


def test_build_artist_event_blocks_falls_back_when_no_shows_found(monkeypatch):
    monkeypatch.setattr(lm.pipeline, "search_web", lambda kw, limit=8: [])

    blocks = lm.build_artist_event_blocks("Unknown Artist")

    assert len(blocks) == 1
    assert blocks[0].general_info.title == "Unknown Artist ticket search"


def test_round_name_keeps_additional_performance_prefix():
    text = "【追加公演・抽選先行】 2026年4月25日(土)10:00～5月10日(日)23:59"
    page = lm.Page("https://horipro-stage.jp/stage/example/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert rounds[0].name == "追加公演・抽選先行"


def test_events_api_exposes_honest_venue_label(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "YOASOBI", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    tour = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="YOASOBI",
            official_page="https://www.yoasobi-music.jp/live#20261024-aaa111",
            title="YOASOBI ASIA 10-CITY DOME & STADIUM TOUR 2026",
            summary="",
            event_dates=("2026年10月24日",),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    with_venue = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="YOASOBI",
            official_page="https://www.yoasobi-music.jp/live#20260731-bbb222",
            title="YOASOBI at Tokyo",
            summary="",
            event_dates=("2026年7月31日",),
            venues=("東京 有明アリーナ",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), tour, now="2026-06-02T00:00:00+00:00", watch_id=watch.id)
    lm.save_blocks(str(db_path), with_venue, now="2026-06-02T00:00:00+00:00", watch_id=watch.id)

    labels = {event["title"]: event["venue_label"] for event in lm.recent_events(str(db_path))}
    assert labels["YOASOBI ASIA 10-CITY DOME & STADIUM TOUR 2026"] == "Multiple cities"
    assert labels["YOASOBI at Tokyo"] == "東京 有明アリーナ"
    locations = {event["title"]: event["event_locations"] for event in lm.recent_events(str(db_path))}
    assert locations["YOASOBI ASIA 10-CITY DOME & STADIUM TOUR 2026"] == []
    assert locations["YOASOBI at Tokyo"] == [
        {
            "location": "東京",
            "city": "東京",
            "venue": "東京 有明アリーナ",
            "date": "2026年7月31日",
        }
    ]


def test_related_events_require_reliable_exact_source_and_stay_account_scoped(tmp_path):
    db_path = str(tmp_path / "related-events.sqlite3")
    alice = lm.create_user(db_path, "alice-related@example.com", "correct horse battery")
    bob = lm.create_user(db_path, "bob-related@example.com", "correct horse battery")

    def save_for(user_id, keyword, title, url, dates):
        watch = lm.add_watch(db_path, keyword, kind=lm.WATCH_KIND_EVENT, user_id=user_id)
        lm.save_blocks(
            db_path,
            lm.AppBlocks(
                general_info=lm.EventInfo(
                    keyword=keyword,
                    official_page=url,
                    title=title,
                    summary="",
                    event_dates=dates,
                    venues=("Example Hall",),
                    ticket_links=(),
                    organizers=("Example Productions",),
                ),
                ticket_info=(),
            ),
            watch_id=watch.id,
        )

    save_for(alice.id, "Alice One", "Alice One Live", "https://official.example/alice-one", ("2026-10-01",))
    save_for(alice.id, "Alice Two", "Alice Two Live", "https://official.example/alice-two", ("2026-11-01",))
    save_for(alice.id, "Unscheduled", "Unscheduled Live", "https://official.example/unscheduled", ())
    save_for(bob.id, "Bob Only", "Bob Only Live", "https://official.example/bob-only", ("2026-12-01",))

    alice_events = {event["title"]: event for event in lm.recent_events(db_path, user_id=alice.id)}
    related = alice_events["Alice One Live"]["related_events"]
    assert [item["title"] for item in related] == ["Alice Two Live"]
    assert related[0]["recommendation_reasons"][:2] == [
        "Shared organizer: Example Productions",
        "Shared venue: Example Hall",
    ]
    assert alice_events["Unscheduled Live"]["related_events"] == []
    assert all(item["title"] != "Bob Only Live" for item in related)

    page = lm.render_event_detail_page(db_path, int(alice_events["Alice One Live"]["id"]), alice.id)
    assert "Related Saved Events" in page
    assert "Shared organizer: Example Productions" in page
    assert "Bob Only Live" not in page


def test_auth_account_and_token_lifecycle(tmp_path):
    db_path = str(tmp_path / "auth.sqlite3")
    user = lm.create_user(db_path, "User@Example.com", "correct horse battery")
    # Email is normalised; password is never stored in the clear.
    assert user.email == "user@example.com"
    assert user.id > 0

    # Duplicate registration and weak inputs are rejected.
    with pytest.raises(ValueError):
        lm.create_user(db_path, "user@example.com", "another password")
    with pytest.raises(ValueError):
        lm.create_user(db_path, "no-at-sign", "longenough123")
    with pytest.raises(ValueError):
        lm.create_user(db_path, "short@example.com", "tiny")

    # Login verifies the password (case-insensitive email, wrong password fails).
    assert lm.verify_user(db_path, "USER@example.com", "correct horse battery").id == user.id
    assert lm.verify_user(db_path, "user@example.com", "wrong") is None
    assert lm.verify_user(db_path, "nobody@example.com", "whatever") is None

    # A token resolves back to its user, and revocation invalidates it.
    token = lm.issue_token(db_path, user.id)
    assert lm.user_for_token(db_path, token).email == "user@example.com"
    assert lm.user_for_token(db_path, "not-a-real-token") is None
    lm.revoke_token(db_path, token)
    assert lm.user_for_token(db_path, token) is None


def test_account_credentials_and_tokens_have_canonical_size_limits(tmp_path):
    db_path = str(tmp_path / "auth-limits.sqlite3")
    with pytest.raises(ValueError, match="valid email"):
        lm.create_user(db_path, f"{'x' * lm.MAX_EMAIL_LENGTH}@example.com", "correct horse battery")
    with pytest.raises(ValueError, match="valid email"):
        lm.create_user(db_path, "space inside@example.com", "correct horse battery")
    with pytest.raises(ValueError, match="1024 characters or fewer"):
        lm.create_user(db_path, "user@example.com", "x" * (lm.MAX_PASSWORD_LENGTH + 1))

    user = lm.create_user(db_path, "user@example.com", "correct horse battery")
    token = lm.issue_token(db_path, user.id)
    calendar_token = lm.issue_calendar_token(db_path, user.id)
    oversized = "x" * (lm.MAX_AUTH_TOKEN_LENGTH + 1)
    assert lm.verify_user(db_path, "user@example.com", "x" * (lm.MAX_PASSWORD_LENGTH + 1)) is None
    assert lm.user_for_token(db_path, oversized) is None
    assert lm.user_id_for_calendar_token(db_path, oversized) is None
    assert lm.revoke_token(db_path, oversized) is False
    assert lm.user_for_token(db_path, token).id == user.id
    assert lm.user_id_for_calendar_token(db_path, calendar_token) == user.id


def test_per_user_watch_subscription_scoping(tmp_path):
    db_path = str(tmp_path / "scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    # Alice subscribes to two watches; Bob to one — and one keyword is shared.
    lm.add_watch(db_path, "YOASOBI", kind=lm.WATCH_KIND_ARTIST, user_id=alice.id)
    lm.add_watch(db_path, "Lion King", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    lm.add_watch(db_path, "YOASOBI", kind=lm.WATCH_KIND_ARTIST, user_id=bob.id)

    assert {w.keyword for w in lm.list_watches(db_path, user_id=alice.id)} == {"YOASOBI", "Lion King"}
    assert {w.keyword for w in lm.list_watches(db_path, user_id=bob.id)} == {"YOASOBI"}

    # The shared keyword is one canonical row (scraped once), not per-user copies.
    all_watches = lm.list_watches(db_path)
    assert sum(1 for w in all_watches if w.keyword == "YOASOBI") == 1
    # Unscoped (CLI/anonymous) still sees the whole shared workspace.
    assert {"YOASOBI", "Lion King"} <= {w.keyword for w in all_watches}


def test_per_user_watch_preferences_do_not_overwrite_shared_keyword(tmp_path):
    db_path = str(tmp_path / "watch-preferences.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")

    alice_watch = lm.add_watch(
        db_path,
        "Shared Tour",
        kind=lm.WATCH_KIND_EVENT,
        tags="musical,alice",
        preferred_regions="Tokyo",
        preferred_venues="Imperial Theatre",
        alert_preferences="lottery_closing_soon",
        user_id=alice.id,
    )
    bob_watch = lm.add_watch(
        db_path,
        "Shared Tour",
        kind=lm.WATCH_KIND_ARTIST,
        tags="concert,bob",
        preferred_regions="Osaka",
        preferred_venues="Festival Hall",
        alert_preferences="payment_due_soon",
        user_id=bob.id,
    )

    assert alice_watch.id == bob_watch.id
    listed_alice = lm.list_watches(db_path, user_id=alice.id)[0]
    listed_bob = lm.list_watches(db_path, user_id=bob.id)[0]
    assert (listed_alice.tags, listed_alice.preferred_regions, listed_alice.preferred_venues) == (
        "musical,alice",
        "Tokyo",
        "Imperial Theatre",
    )
    assert listed_alice.alert_preferences == "lottery_closing_soon"
    assert (listed_bob.tags, listed_bob.preferred_regions, listed_bob.preferred_venues) == (
        "concert,bob",
        "Osaka",
        "Festival Hall",
    )
    assert listed_bob.alert_preferences == "payment_due_soon"
    assert listed_alice.kind == lm.WATCH_KIND_EVENT
    assert listed_bob.kind == lm.WATCH_KIND_ARTIST
    assert lm.list_watches(db_path, kind=lm.WATCH_KIND_ARTIST, user_id=alice.id) == []
    assert [watch.id for watch in lm.list_watches(db_path, kind=lm.WATCH_KIND_ARTIST, user_id=bob.id)] == [bob_watch.id]

    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Shared Tour",
            official_page="https://official.example/shared-tour",
            title="Shared Tour 2026",
            summary="",
            event_dates=("2026年10月1日",),
            venues=("Tokyo Imperial Theatre",),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                url="https://official.example/shared-tour",
                name="第1次抽選先行",
                application_start_at="2026-05-20",
                application_end_at="2026-06-02",
            ),
        ),
    )
    lm.save_blocks(db_path, blocks, now="2026-06-01T00:00:00+00:00", watch_id=alice_watch.id)
    assert lm.recent_events(db_path, user_id=alice.id)[0]["watch_kind"] == lm.WATCH_KIND_EVENT
    assert lm.recent_events(db_path, user_id=bob.id)[0]["watch_kind"] == lm.WATCH_KIND_ARTIST
    assert {alert["type"] for alert in lm.recent_alerts(db_path, user_id=alice.id)} == {"lottery_closing_soon"}
    assert lm.recent_alerts(db_path, user_id=bob.id) == []

    # Re-adding is also the preference-edit operation used by web and native
    # clients. Updating Alice must not change Bob or leak personal filters into
    # the canonical discovery row used by the local CLI.
    lm.add_watch(
        db_path,
        "Shared Tour",
        kind=lm.WATCH_KIND_EVENT,
        tags="musical,updated",
        preferred_regions="Kanagawa",
        preferred_venues="KAAT",
        alert_preferences="results_today",
        user_id=alice.id,
    )
    assert lm.list_watches(db_path, user_id=alice.id)[0].tags == "musical,updated"
    assert lm.list_watches(db_path, user_id=alice.id)[0].alert_preferences == "results_today"
    assert lm.list_watches(db_path, user_id=bob.id)[0] == listed_bob
    canonical = lm.list_watches(db_path)[0]
    assert canonical.tags == ""
    assert canonical.preferred_regions == ""
    assert canonical.preferred_venues == ""
    assert canonical.alert_preferences == lm.DEFAULT_ALERT_PREFERENCES

    with lm.connect(db_path) as connection:
        columns = lm.table_columns(connection, "user_watches")
    assert {"kind", "tags", "preferred_regions", "preferred_venues", "alert_preferences"} <= columns


def test_per_user_watch_muting_keeps_shared_canonical_watch_active(tmp_path):
    db_path = str(tmp_path / "watch-mute-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_watch = lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    bob_watch = lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Shared Show",
            official_page="https://official.example/shared",
            title="Shared Show Tour",
            summary="",
            event_dates=("2026年6月20日",),
            venues=("Example Hall",),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                url="https://official.example/shared",
                name="第1次抽選先行",
                application_start_at="2026-06-01",
                application_end_at="2026-06-02",
            ),
        ),
    )
    lm.save_blocks(db_path, blocks, now="2026-06-01T00:00:00+00:00", watch_id=alice_watch.id)
    lm.add_subscription(db_path, str(alice_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, user_id=alice.id)

    assert alice_watch.id == bob_watch.id
    assert lm.remove_watch(db_path, "Shared Show", user_id=alice.id) is True
    assert lm.list_watches(db_path, user_id=alice.id) == []
    assert [watch.muted for watch in lm.list_watches(db_path, include_muted=True, user_id=alice.id)] == [True]
    assert [watch.keyword for watch in lm.list_watches(db_path, user_id=bob.id)] == ["Shared Show"]
    assert lm.list_watches(db_path, user_id=bob.id)[0].muted is False
    assert lm.recent_events(db_path, user_id=alice.id) == []
    assert [event["title"] for event in lm.recent_events(db_path, include_muted_watches=True, user_id=alice.id)] == [
        "Shared Show Tour"
    ]
    assert [event["title"] for event in lm.recent_events(db_path, user_id=bob.id)] == ["Shared Show Tour"]
    assert lm.upcoming_priority_rows(db_path, user_id=alice.id) == []
    assert [row["event_title"] for row in lm.upcoming_priority_rows(db_path, user_id=bob.id)] == ["Shared Show Tour"]
    assert lm.recent_alerts(db_path, user_id=alice.id) == []
    assert lm.pending_notifications(db_path, now="2026-06-01T00:00:00+00:00", user_id=alice.id) == []

    assert lm.set_watch_muted(db_path, "Shared Show", False, user_id=alice.id) is True
    assert [watch.keyword for watch in lm.list_watches(db_path, user_id=alice.id)] == ["Shared Show"]
    assert [event["title"] for event in lm.recent_events(db_path, user_id=alice.id)] == ["Shared Show Tour"]


def test_recent_events_scope_to_subscribed_user(tmp_path):
    db_path = str(tmp_path / "events-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_watch = lm.add_watch(db_path, "Alice Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    bob_watch = lm.add_watch(db_path, "Bob Show", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)

    def event_blocks(keyword, title, url):
        return lm.AppBlocks(
            general_info=lm.EventInfo(
                keyword=keyword, official_page=url, title=title, summary="",
                event_dates=("2026年7月10日",), venues=("Hall",), ticket_links=(),
            ),
            ticket_info=(),
        )

    lm.save_blocks(db_path, event_blocks("Alice Show", "Alice Event", "https://a.example/"), watch_id=alice_watch.id)
    lm.save_blocks(db_path, event_blocks("Bob Show", "Bob Event", "https://b.example/"), watch_id=bob_watch.id)

    assert {e["title"] for e in lm.recent_events(db_path, user_id=alice.id)} == {"Alice Event"}
    assert {e["title"] for e in lm.recent_events(db_path, user_id=bob.id)} == {"Bob Event"}
    # Unscoped (CLI/anonymous) still sees the whole shared workspace.
    assert {"Alice Event", "Bob Event"} <= {e["title"] for e in lm.recent_events(db_path)}


def test_watch_sources_scope_to_owner_without_hiding_global_public_sources(tmp_path):
    db_path = str(tmp_path / "source-owner-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_watch = lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    bob_watch = lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)
    global_source = lm.add_watch_source(db_path, str(alice_watch.id), "https://official.example/shared", "Official")
    alice_source = lm.add_watch_source(
        db_path, str(alice_watch.id), "https://fan.example/alice", "Alice FC", private_note=True, user_id=alice.id
    )
    bob_source = lm.add_watch_source(
        db_path, str(bob_watch.id), "https://fan.example/bob", "Bob FC", private_note=True, user_id=bob.id
    )

    assert global_source.user_id == 0
    assert alice_source.user_id == alice.id
    assert bob_source.user_id == bob.id
    assert {source.label for source in lm.list_watch_sources(db_path, user_id=alice.id)} == {"Official", "Alice FC"}
    assert {source.label for source in lm.list_watch_sources(db_path, user_id=bob.id)} == {"Official", "Bob FC"}
    assert lm.remove_watch_source(db_path, str(bob_source.id), user_id=alice.id) is False
    assert {source.label for source in lm.list_watch_sources(db_path, user_id=bob.id)} == {"Official", "Bob FC"}
    assert lm.remove_watch_source(db_path, str(alice_source.id), user_id=alice.id) is True
    assert {source.label for source in lm.list_watch_sources(db_path, user_id=alice.id)} == {"Official"}


def test_sources_and_alerts_scope_to_subscribed_user(tmp_path):
    db_path = str(tmp_path / "sa-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_watch = lm.add_watch(db_path, "Alice Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    bob_watch = lm.add_watch(db_path, "Bob Show", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)

    lm.add_watch_source(db_path, str(alice_watch.id), "https://a.example/src", "Alice Src", False)
    lm.add_watch_source(db_path, str(bob_watch.id), "https://b.example/src", "Bob Src", False)
    assert {s.label for s in lm.list_watch_sources(db_path, user_id=alice.id)} == {"Alice Src"}
    assert {s.label for s in lm.list_watch_sources(db_path, user_id=bob.id)} == {"Bob Src"}

    # A round closing the day after `now` persists a lifecycle alert under
    # Alice's event, which must be scoped to Alice only.
    now = "2026-07-10T00:00:00+00:00"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Alice Show", official_page="https://a.example/ev", title="Alice Event",
            summary="", event_dates=(), venues=(), ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official", url="https://a.example/ev", name="第1次抽選先行",
                application_start_at="2026-07-01", application_end_at="2026-07-11",
            ),
        ),
    )
    lm.save_blocks(db_path, blocks, now=now, watch_id=alice_watch.id)
    assert lm.recent_alerts(db_path, user_id=alice.id)
    assert not lm.recent_alerts(db_path, user_id=bob.id)
    # Unscoped still sees both users' sources.
    assert {"Alice Src", "Bob Src"} <= {s.label for s in lm.list_watch_sources(db_path)}


def test_password_hash_is_salted_and_verifiable():
    first_hash, first_salt = lm.hash_password("hunter2hunter2")
    second_hash, second_salt = lm.hash_password("hunter2hunter2")
    # Different salts -> different stored hashes for the same password.
    assert first_salt != second_salt
    assert first_hash != second_hash
    assert lm.password_matches("hunter2hunter2", first_hash, first_salt)
    assert not lm.password_matches("hunter2hunter2 wrong", first_hash, first_salt)


def test_storage_connect_seam(tmp_path, monkeypatch):
    # The seam opens SQLite exactly as before and recognises a Postgres target.
    db = tmp_path / "seam.sqlite3"
    with lm.connect(str(db)) as connection:
        lm.init_db(connection)
    assert db.exists()
    assert lm.resolve_target("explicit.sqlite3") == "explicit.sqlite3"
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://user@host/deployed")
    assert lm.resolve_target() == "postgresql://user@host/deployed"
    assert lm.resolve_target(lm.DEFAULT_DB_PATH) == "postgresql://user@host/deployed"
    assert lm.resolve_target("explicit.sqlite3") == "explicit.sqlite3"
    assert lm.is_postgres_url("postgresql://user@host/db")
    assert not lm.is_postgres_url("chusennote.sqlite3")
    assert lm.dialect_of("postgres://u@h/db") == "postgres"
    assert lm.dialect_of("chusennote.sqlite3") == "sqlite"


def test_subscription_migration_rebuilds_old_sqlite_unique_key(tmp_path):
    db_path = str(tmp_path / "old-subscriptions.sqlite3")
    with lm.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE notification_subscriptions (
                id INTEGER PRIMARY KEY,
                watch_id INTEGER NOT NULL,
                scope TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                round_key TEXT NOT NULL DEFAULT '',
                channels TEXT NOT NULL DEFAULT 'feed',
                lead_days TEXT NOT NULL DEFAULT '7,1,0',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(watch_id, scope, location, round_key)
            )
            """
        )
        lm.init_db(connection)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notification_subscriptions'"
        ).fetchone()[0]

    assert "UNIQUE(user_id, watch_id, scope, location, round_key)" in table_sql.replace("\n", " ")


POSTGRES_TEST_URL_ENV = "CHUSENNOTE_TEST_DATABASE_URL"
_POSTGRES_TABLES = (
    "user_watches",
    "calendar_tokens",
    "api_tokens",
    "users",
    "notification_log",
    "notification_subscriptions",
    "device_tokens",
    "alert_log",
    "snapshots",
    "ticket_rounds",
    "sources",
    "watch_sources",
    "events",
    "watched_keywords",
)


@pytest.mark.skipif(
    not os.environ.get(POSTGRES_TEST_URL_ENV),
    reason="set CHUSENNOTE_TEST_DATABASE_URL to run the Postgres backend test",
)
def test_postgres_backend_round_trips_core_flows(monkeypatch):
    """End-to-end CRUD on a real Postgres database, proving the dialect adapter:
    schema creation, an upsert with ON CONFLICT, a round insert, and the read
    models all work unchanged against Postgres."""
    url = os.environ[POSTGRES_TEST_URL_ENV]
    with lm.connect(url) as connection:
        for table in _POSTGRES_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    watch = lm.add_watch(url, "PG Demo", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="PG Demo",
            official_page="https://official.example/pg",
            title="PG Demo Event",
            summary="",
            event_dates=("2026年6月20日",),
            venues=("Example Hall",),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                url="https://official.example/pg",
                name="第1次抽選先行",
                application_start_at="2026-06-10",
                application_end_at="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )
    lm.save_blocks(url, blocks, now="2026-06-01T00:00:00+00:00", watch_id=watch.id)

    events = lm.recent_events(url)
    event = next(event for event in events if event["title"] == "PG Demo Event")
    assert event["venue_label"] == "Example Hall"
    assert event["rounds"][0]["schedule_label"] == "Apply 2026-06-10 – 2026-06-18 · Results 2026-06-22"
    assert any(watch.keyword == "PG Demo" for watch in lm.list_watches(url))
    assert {alert["type"] for alert in lm.recent_alerts(url)} == {
        "new_official_page",
        "new_lottery_round",
    }
    postgres_health = lm.api_health(url)
    assert postgres_health["schema_version"] == lm.DB_SCHEMA_VERSION
    assert postgres_health["db_path"] == "postgresql"
    assert url not in json.dumps(postgres_health)
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", url)
    default_health = lm.api_health(lm.DEFAULT_DB_PATH)
    assert default_health["db_path"] == "postgresql"
    assert default_health["saved_events"] == postgres_health["saved_events"]
    assert url not in json.dumps(default_health)

    # Accounts and bearer tokens round-trip through Postgres too.
    user = lm.create_user(url, "pg@example.com", "correct horse battery")
    token = lm.issue_token(url, user.id)
    assert lm.user_for_token(url, token).email == "pg@example.com"
    assert lm.verify_user(url, "pg@example.com", "correct horse battery").id == user.id

    # Upgrade the old one-token-per-user constraint without losing its token.
    first_calendar = lm.issue_calendar_token(url, user.id)
    with lm.connect(url) as connection:
        connection.execute("ALTER TABLE calendar_tokens ADD CONSTRAINT calendar_tokens_user_id_key UNIQUE(user_id)")
    second_calendar = lm.issue_calendar_token(url, user.id)
    assert lm.user_id_for_calendar_token(url, first_calendar) == user.id
    assert lm.user_id_for_calendar_token(url, second_calendar) == user.id

    # Per-user watch subscriptions scope on Postgres too.
    lm.add_watch(url, "PG Subscribed", kind=lm.WATCH_KIND_EVENT, user_id=user.id)
    assert {watch.keyword for watch in lm.list_watches(url, user_id=user.id)} == {"PG Subscribed"}
    subscription = lm.add_subscription(url, "PG Subscribed", lm.NOTIFY_SCOPE_EVENT_ALL, user_id=user.id)
    assert subscription.user_id == user.id


def test_adapt_sql_rewrites_sqlite_isms_for_postgres():
    # SQLite is untouched.
    assert lm.adapt_sql("SELECT id WHERE x = ?", "sqlite") == "SELECT id WHERE x = ?"
    # Postgres: placeholders, identity column, and INSERT OR IGNORE.
    assert lm.adapt_sql("SELECT id WHERE x = ?", "postgres") == "SELECT id WHERE x = %s"
    assert "GENERATED BY DEFAULT AS IDENTITY" in lm.adapt_sql(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", "postgres"
    )
    adapted = lm.adapt_sql("INSERT OR IGNORE INTO t(a, b) VALUES (?, ?)", "postgres")
    assert adapted == "INSERT INTO t(a, b) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    # A literal % (LIKE pattern) is escaped before placeholders are converted.
    assert (
        lm.adapt_sql("SELECT id WHERE u LIKE 'keyword:%' AND w = ?", "postgres")
        == "SELECT id WHERE u LIKE 'keyword:%%' AND w = %s"
    )
    assert lm.split_statements("CREATE TABLE a (x INT); CREATE TABLE b (y INT);") == [
        "CREATE TABLE a (x INT)",
        "CREATE TABLE b (y INT)",
    ]
    # A ; inside a -- comment must not split the statement.
    assert lm.split_statements("-- one row; each user subscribes\nCREATE TABLE c (z INT);") == [
        "CREATE TABLE c (z INT)",
    ]


def test_round_type_and_membership_labels():
    assert lm.round_type_label("fc") == "Fan club"
    assert lm.round_type_label("platform") == "Platform presale"
    assert lm.round_type_label("unknown") == ""
    assert lm.membership_label("yes") == "Membership required"
    assert lm.membership_label("no") == "No membership needed"
    assert lm.membership_label("unknown") == ""


def test_round_schedule_label_builds_when_to_act_line():
    label = lm.round_schedule_label(
        {
            "application_start_at": "2026-06-10",
            "application_end_at": "2026-06-18",
            "results_date": "2026-06-22",
            "payment_end_at": "2026-06-25",
            "general_sale_date": "2026-07-04",
        }
    )
    assert label == "Apply 2026-06-10 – 2026-06-18 · Results 2026-06-22 · Pay by 2026-06-25 · Sale 2026-07-04"
    # Partial windows and empty rounds degrade gracefully.
    assert lm.round_schedule_label({"application_end_at": "2026-06-18"}) == "Apply by 2026-06-18"
    assert lm.round_schedule_label(
        {"trade_start_at": "2026-07-01", "trade_end_at": "2026-07-03"}
    ) == "Resale 2026-07-01 – 2026-07-03"
    assert lm.round_schedule_label({}) == ""


def test_events_api_exposes_round_schedule_label(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Schedule Demo", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Schedule Demo",
            official_page="https://official.example/schedule",
            title="Schedule Demo Event",
            summary="",
            event_dates=("2026年6月20日",),
            venues=("Example Hall",),
            ticket_links=(),
            organizers=("Example Productions",),
            lineup=("Example Lead", "Example Guest"),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                url="https://official.example/schedule",
                name="第1次抽選先行",
                application_start_at="2026-06-10",
                application_end_at="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-01T00:00:00+00:00", watch_id=watch.id)

    event = lm.recent_events(str(db_path))[0]
    assert event["rounds"][0]["schedule_label"] == "Apply 2026-06-10 – 2026-06-18 · Results 2026-06-22"
    assert event["organizers"] == ["Example Productions"]
    assert event["lineup"] == ["Example Lead", "Example Guest"]


def test_human_status_maps_codes_to_plain_wording():
    assert lm.human_status("closing_soon") == "Closing soon"
    assert lm.human_status("results_today") == "Results today"
    assert lm.human_status("open") == "Open now"
    assert lm.human_status("trade_open") == "Resale open"
    assert lm.human_status("trade_closing_soon") == "Resale closing soon"
    # Unknown / unrecognised render empty so the UI can hide them.
    assert lm.human_status("unknown") == ""
    assert lm.human_status(None) == ""


def test_human_event_status_maps_lifecycle_codes_to_plain_wording():
    assert lm.human_event_status("watching") == "Watching"
    assert lm.human_event_status("official_found") == "Official page found"
    assert lm.human_event_status("ticket_links_found") == "Ticket links found"
    assert lm.human_event_status("lottery_found") == "Ticket rounds found"
    assert lm.human_event_status("lottery_open") == "Ticket window open"
    assert lm.human_event_status("unknown") == ""


def test_human_alert_type_maps_stable_codes_to_plain_wording():
    assert lm.human_alert_type("new_official_page") == "Official page found"
    assert lm.human_alert_type("lottery_closing_soon") == "Lottery closing soon"
    assert lm.human_alert_type("trade_opened") == "Official resale opened"
    assert lm.human_alert_type("future_alert") == "Future alert"
    assert lm.human_alert_type(None) == ""
    assert lm.human_alert_preferences("new_official_page,trade_opened") == (
        "Official page found, Official resale opened"
    )
    assert lm.human_alert_preferences("") == "none"


def test_web_alert_item_prefers_readable_type_label():
    rendered = lm.web.render_alert_item(
        {
            "type": "lottery_closing_soon",
            "type_label": "Lottery closing soon",
            "event": "Example Tour",
        }
    )

    assert "Lottery closing soon" in rendered
    assert "lottery_closing_soon" not in rendered


def test_upcoming_api_exposes_status_label(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Status Demo", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    soon = (dt.date(2026, 6, 1) + dt.timedelta(days=1)).isoformat()
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Status Demo",
            official_page="https://official.example/status",
            title="Status Demo Event",
            summary="",
            event_dates=("2026年6月20日",),
            venues=("Example Hall",),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                url="https://official.example/status",
                name="第1次抽選先行",
                application_start_at="2026-06-01",
                application_end_at=soon,
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-01T00:00:00+00:00", watch_id=watch.id)

    rows = lm.upcoming_priority_rows(str(db_path))
    assert rows, "expected at least one upcoming round"
    assert rows[0]["status_label"] == lm.human_status(rows[0]["status"])
    assert rows[0]["status_label"] != ""


def test_save_blocks_prunes_search_fallback_once_real_show_exists(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "YOASOBI", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    # First pass found no shows: only the portal-search fallback is saved.
    fallback = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="YOASOBI",
            official_page="https://t.pia.jp/pia/search_all.do?kw=YOASOBI",
            title="YOASOBI ticket search",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), fallback, now="2026-06-02T00:00:00+00:00", watch_id=watch.id)

    # A later pass discovers a real tour; the stale fallback must be removed.
    show = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="YOASOBI",
            official_page="https://www.yoasobi-music.jp/live#20261024-aaa111",
            title="YOASOBI ASIA TOUR 2026",
            summary="2026-10-24",
            event_dates=("2026年10月24日",),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), show, now="2026-06-03T00:00:00+00:00", watch_id=watch.id)

    with sqlite3.connect(db_path) as connection:
        titles = [row[0] for row in connection.execute("SELECT canonical_title FROM events WHERE watch_id = ?", (watch.id,))]

    assert titles == ["YOASOBI ASIA TOUR 2026"]


def _subscription_event_blocks(keyword="Sub", venues=("EXシアター有明",), rounds=None):
    if rounds is None:
        rounds = (
            lm.TicketRound(
                source="pia", platform="pia", url="https://t.pia.jp/x", name="第1次抽選先行",
                lottery_start="2026-06-23", lottery_end="2026-06-30", results_date="2026-07-05",
                evidence="東京 EXシアター有明 抽選先行",
            ),
        )
    return lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword=keyword, official_page="https://official.example/", title=f"{keyword} Tour",
            summary="", event_dates=("2026年8月10日",), venues=venues, ticket_links=(),
        ),
        ticket_info=rounds,
    )


def test_notification_run_delivers_due_reminders_and_is_idempotent(tmp_path):
    db_path = tmp_path / "n.sqlite3"
    watch = lm.add_watch(str(db_path), "Sub", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(str(db_path), _subscription_event_blocks("Sub"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    lm.add_subscription(str(db_path), "Sub", lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed")

    # 7 days before the 2026-06-23 open date.
    first = lm.run_notifications(str(db_path), now="2026-06-16T00:00:00+00:00")
    assert any(n["label"] == "Lottery application opens" and n["lead_days"] == 7 for n in first)
    assert lm.run_notifications(str(db_path), now="2026-06-16T00:00:00+00:00") == []
    # 1 day before the 2026-06-30 close date.
    later = lm.run_notifications(str(db_path), now="2026-06-29T00:00:00+00:00")
    assert any(n["label"] == "Lottery application closes" and n["lead_days"] == 1 for n in later)
    assert lm.notification_feed(str(db_path))


def test_notification_run_includes_official_resale_open_and_close_dates(tmp_path):
    db_path = tmp_path / "resale-notifications.sqlite3"
    watch = lm.add_watch(str(db_path), "Resale", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    rounds = (
        lm.TicketRound(
            source="official",
            url="https://official.example/resale",
            name="公式トレード",
            trade_start_at="2026-07-01",
            trade_end_at="2026-07-03",
        ),
    )
    lm.save_blocks(
        str(db_path),
        _subscription_event_blocks("Resale", rounds=rounds),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(str(db_path), "Resale", lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed")

    opening = lm.run_notifications(str(db_path), now="2026-06-24T00:00:00+00:00")
    closing = lm.run_notifications(str(db_path), now="2026-06-26T00:00:00+00:00")

    assert any(item["label"] == "Official resale opens" and item["lead_days"] == 7 for item in opening)
    assert any(item["label"] == "Official resale closes" and item["lead_days"] == 7 for item in closing)


def test_notification_run_retries_only_failed_channels_without_duplicate_feed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-retry.sqlite3")
    watch = lm.add_watch(db_path, "Retry", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Retry"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(
        db_path,
        "Retry",
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels="feed,email,push",
    )
    lm.register_device(db_path, "retry-device", platform="android", user_id=0)
    email_calls = []
    push_calls = []

    def fake_email(notification):
        email_calls.append(notification["notification_key"])
        return True

    def fake_push(notification, devices, invalid_tokens=None):
        push_calls.append((notification["notification_key"], [device.token for device in devices]))
        return len(push_calls) > 1

    monkeypatch.setattr(lm.notifications, "send_email_notification", fake_email)
    monkeypatch.setattr(lm.notifications, "send_push_notification", fake_push)

    first = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    assert first[0]["delivered"] == {"feed": True, "email": True, "push": False}

    retried = lm.run_notifications(db_path, now="2026-06-16T01:00:00+00:00", user_id=0)
    assert retried[0]["delivered"] == {"feed": True, "email": True, "push": True}
    assert len(email_calls) == 1
    assert len(push_calls) == 2
    assert push_calls[1][1] == ["retry-device"]

    assert lm.run_notifications(db_path, now="2026-06-16T02:00:00+00:00", user_id=0) == []
    feed = lm.notification_feed(db_path, user_id=0)
    assert len(feed) == 1
    assert feed[0]["delivered"] == {"feed": True, "email": True, "push": True}


def test_notification_runs_in_one_process_do_not_send_the_same_push_concurrently(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-concurrency.sqlite3")
    watch = lm.add_watch(db_path, "Concurrent", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Concurrent"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(db_path, "Concurrent", lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push")
    lm.register_device(db_path, "concurrent-device", platform="ios", user_id=0)
    first_send_started = threading.Event()
    release_first_send = threading.Event()
    push_calls = []
    results = []

    def fake_push(notification, devices, invalid_tokens=None):
        push_calls.append(notification["notification_key"])
        first_send_started.set()
        assert release_first_send.wait(timeout=5)
        return True

    def run_once():
        results.append(lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0))

    monkeypatch.setattr(lm.notifications, "send_push_notification", fake_push)
    first = threading.Thread(target=run_once)
    second = threading.Thread(target=run_once)
    first.start()
    assert first_send_started.wait(timeout=5)
    second.start()
    release_first_send.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert len(push_calls) == 1
    assert sorted(len(result) for result in results) == [0, 1]
    assert len(lm.notification_feed(db_path, user_id=0)) == 1


def test_database_claim_prevents_duplicate_push_after_two_workers_read_pending(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-cross-process.sqlite3")
    watch = lm.add_watch(db_path, "Two Workers", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.save_blocks(db_path, _subscription_event_blocks("Two Workers"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=0)
    lm.register_device(db_path, "two-worker-device", platform="android", user_id=0)
    original_pending = lm.notifications.pending_notifications
    both_read_pending = threading.Barrier(2)
    push_calls = []
    results = []

    def synchronized_pending(*args, **kwargs):
        pending = original_pending(*args, **kwargs)
        both_read_pending.wait(timeout=5)
        return pending

    def fake_push(notification, devices, invalid_tokens=None):
        push_calls.append([device.token for device in devices])
        return True

    def run_worker():
        results.append(
            lm.notifications._run_notifications_unlocked(
                db_path,
                now="2026-06-16T00:00:00+00:00",
                user_id=0,
            )
        )

    monkeypatch.setattr(lm.notifications, "pending_notifications", synchronized_pending)
    monkeypatch.setattr(lm.notifications, "send_push_notification", fake_push)
    workers = [threading.Thread(target=run_worker) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert push_calls == [["two-worker-device"]]
    assert sorted(len(result) for result in results) == [0, 1]


def test_abandoned_notification_claim_is_reclaimed_after_lease(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-stale-claim.sqlite3")
    watch = lm.add_watch(db_path, "Stale Claim", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.save_blocks(db_path, _subscription_event_blocks("Stale Claim"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    subscription = lm.add_subscription(
        db_path,
        str(watch.id),
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels="feed,push",
        user_id=0,
    )
    lm.register_device(db_path, "stale-claim-device", platform="ios", user_id=0)
    pending = lm.pending_notifications(db_path, now="2026-06-16T00:00:00Z", user_id=0)
    notification = pending[0]
    with lm.connect(db_path) as connection:
        lm.init_db(connection)
        assert lm.claim_notification_delivery(
            connection,
            notification["notification_key"],
            subscription.id,
            notification["event_id"],
            notification["channels"],
            {},
            "2026-06-16T00:00:00+00:00",
            notification["_claim_stale_before"],
            existed=False,
            expected_attempt_count=0,
        )

    monkeypatch.setattr(lm.notifications, "send_push_notification", lambda notification, devices, invalid_tokens=None: True)
    assert lm.notification_feed(db_path, user_id=0) == []
    assert lm.run_notifications(db_path, now="2026-06-16T00:14:59Z", user_id=0) == []
    reclaimed = lm.run_notifications(db_path, now="2026-06-16T00:15:01Z", user_id=0)
    assert len(reclaimed) == 1
    with sqlite3.connect(db_path) as connection:
        processing_at, attempts = connection.execute(
            "SELECT processing_at, attempt_count FROM notification_log WHERE notification_key = ?",
            (notification["notification_key"],),
        ).fetchone()
    assert processing_at is None
    assert attempts == 2


def test_notification_claim_is_released_when_delivery_raises(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-claim-error.sqlite3")
    watch = lm.add_watch(db_path, "Claim Error", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.save_blocks(db_path, _subscription_event_blocks("Claim Error"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=0)
    lm.register_device(db_path, "claim-error-device", platform="android", user_id=0)
    monkeypatch.setattr(
        lm.notifications,
        "send_push_notification",
        lambda notification, devices, invalid_tokens=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT processing_at FROM notification_log").fetchone()[0] is None

    monkeypatch.setattr(lm.notifications, "send_push_notification", lambda notification, devices, invalid_tokens=None: True)
    assert len(lm.run_notifications(db_path, now="2026-06-16T00:00:01+00:00", user_id=0)) == 1


def test_authenticated_email_notifications_do_not_use_global_recipient(tmp_path, monkeypatch):
    db_path = str(tmp_path / "email-scope.sqlite3")
    user = lm.create_user(db_path, "alice@example.com", "alice password 1")
    anonymous_watch = lm.add_watch(db_path, "Anon Email", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    user_watch = lm.add_watch(db_path, "User Email", kind=lm.WATCH_KIND_EVENT, user_id=user.id, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Anon Email"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=anonymous_watch.id,
    )
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("User Email"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=user_watch.id,
    )
    lm.add_subscription(db_path, str(anonymous_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,email")
    lm.add_subscription(db_path, str(user_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,email", user_id=user.id)
    emailed = []

    def fake_email(notification):
        emailed.append(notification["event_title"])
        return True

    monkeypatch.setattr(lm.notifications, "send_email_notification", fake_email)

    anonymous = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    authenticated = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=user.id)

    assert emailed == ["Anon Email Tour"]
    assert anonymous[0]["delivered"]["email"] is True
    assert authenticated[0]["delivered"]["email"] is False


def test_fcm_access_token_loads_adc_once_and_refreshes_only_when_needed(monkeypatch):
    class Credentials:
        valid = False
        token = ""

    credentials = Credentials()
    loads = []
    refreshes = []
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(lm.notifications, "_fcm_credentials", None)
    monkeypatch.setattr(lm.notifications, "_fcm_credentials_project_id", "")
    monkeypatch.setattr(lm.notifications, "_fcm_credentials_source", "")

    def fake_load():
        loads.append(True)
        return credentials, "adc-project"

    def fake_refresh(value):
        refreshes.append(value)
        value.valid = True
        value.token = "short-lived-token"

    monkeypatch.setattr(lm.notifications, "_load_fcm_credentials", fake_load)
    monkeypatch.setattr(lm.notifications, "_refresh_fcm_credentials", fake_refresh)

    assert lm.notifications._fcm_access_token() == ("short-lived-token", "adc-project")
    assert lm.notifications._fcm_access_token() == ("short-lived-token", "adc-project")
    assert len(loads) == 1
    assert refreshes == [credentials]


def test_notification_subscription_validates_and_normalizes_channels(tmp_path):
    db_path = str(tmp_path / "notification-channels.sqlite3")
    watch = lm.add_watch(db_path, "Channels", kind=lm.WATCH_KIND_EVENT)

    subscription = lm.add_subscription(
        db_path,
        str(watch.id),
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels=" Slack,feed,slack,LINE ",
    )

    assert subscription.channels == "slack,feed,line"
    with pytest.raises(ValueError, match="Unknown notification channel: pager"):
        lm.add_subscription(
            db_path,
            str(watch.id),
            lm.NOTIFY_SCOPE_EVENT_LOCATION,
            location="Tokyo",
            channels="feed,pager",
        )
    with pytest.raises(ValueError, match="Reminder lead days"):
        lm.add_subscription(
            db_path,
            str(watch.id),
            lm.NOTIFY_SCOPE_EVENT_LOCATION,
            location="Osaka",
            lead_days="tomorrow,1",
        )


def test_slack_discord_and_line_delivery_use_provider_contracts(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"ok"

    monkeypatch.setenv(lm.SLACK_WEBHOOK_URL_ENV, "https://hooks.slack.com/services/T/B/secret")
    monkeypatch.setenv(lm.DISCORD_WEBHOOK_URL_ENV, "https://discord.com/api/webhooks/123/secret")
    monkeypatch.setenv(lm.LINE_CHANNEL_ACCESS_TOKEN_ENV, "line-access-token")
    monkeypatch.setenv(lm.LINE_TARGET_ID_ENV, "U123")
    monkeypatch.setattr(
        lm.notifications,
        "_open_external_notification_request",
        lambda request: requests.append(request) or Response(),
    )
    notification = {
        "notification_key": "stable-reminder-key",
        "event_title": "Example Tour",
        "label": "Lottery application closes",
        "subject": "First lottery",
        "date": "2026-10-01",
        "lead_days": 1,
        "url": "https://official.example/tickets",
    }

    assert lm.send_slack_notification(notification) is True
    assert lm.send_discord_notification(notification) is True
    assert lm.send_line_notification(notification) is True

    assert [request.full_url for request in requests] == [
        "https://hooks.slack.com/services/T/B/secret",
        "https://discord.com/api/webhooks/123/secret",
        "https://api.line.me/v2/bot/message/push",
    ]
    assert "Lottery application closes in 1 day" in json.loads(requests[0].data)["text"]
    assert "Lottery application closes in 1 day" in json.loads(requests[1].data)["content"]
    line_payload = json.loads(requests[2].data)
    assert line_payload["to"] == "U123"
    assert line_payload["messages"][0]["type"] == "text"
    assert requests[2].get_header("Authorization") == "Bearer line-access-token"
    assert uuid.UUID(requests[2].get_header("X-line-retry-key")).version == 5


def test_external_notification_urls_reject_redirects_and_untrusted_hosts(monkeypatch):
    monkeypatch.setenv(lm.SLACK_WEBHOOK_URL_ENV, "https://hooks.slack.com.evil.example/services/steal")
    monkeypatch.setenv(lm.DISCORD_WEBHOOK_URL_ENV, "https://discord.com.evil.example/api/webhooks/steal")
    monkeypatch.setattr(
        lm.notifications,
        "_open_external_notification_request",
        lambda request: pytest.fail("untrusted webhook must not be contacted"),
    )

    assert lm.send_slack_notification({}) is False
    assert lm.send_discord_notification({}) is False
    handler = lm.notifications._NoExternalNotificationRedirects()
    request = urllib.request.Request("https://hooks.slack.com/services/start")
    assert handler.redirect_request(request, None, 307, "redirect", {}, "https://evil.example/steal") is None


def test_external_notification_failures_retry_only_failed_channels(tmp_path, monkeypatch):
    db_path = str(tmp_path / "external-channel-retry.sqlite3")
    watch = lm.add_watch(db_path, "External Retry", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("External Retry"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(
        db_path,
        str(watch.id),
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels="feed,slack,discord,line",
        user_id=0,
    )
    calls = {"slack": 0, "discord": 0, "line": 0}

    def sender(channel, succeed_after=1):
        def send(notification):
            calls[channel] += 1
            return calls[channel] >= succeed_after
        return send

    monkeypatch.setattr(lm.notifications, "send_slack_notification", sender("slack"))
    monkeypatch.setattr(lm.notifications, "send_discord_notification", sender("discord", succeed_after=2))
    monkeypatch.setattr(lm.notifications, "send_line_notification", sender("line"))

    first = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    second = lm.run_notifications(db_path, now="2026-06-16T00:00:01+00:00", user_id=0)

    assert first[0]["delivered"] == {"feed": True, "slack": True, "discord": False, "line": True}
    assert second[0]["delivered"] == {"feed": True, "slack": True, "discord": True, "line": True}
    assert calls == {"slack": 1, "discord": 2, "line": 1}
    assert len(lm.notification_feed(db_path, user_id=0)) == 1


def test_external_notification_status_and_account_privacy_boundary(tmp_path, monkeypatch):
    db_path = str(tmp_path / "external-channel-status.sqlite3")
    local_watch = lm.add_watch(db_path, "Local External", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.add_subscription(
        db_path,
        str(local_watch.id),
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels="slack,discord,line",
        user_id=0,
    )

    missing = lm.notification_configuration_status(db_path)
    assert missing["ok"] is False
    assert missing["external"]["slack"] == {
        "anonymous_subscriptions": 1,
        "account_subscriptions": 0,
        "configured": False,
    }
    assert "slack subscriptions require valid local delivery credentials" in missing["issues"]
    assert "discord subscriptions require valid local delivery credentials" in missing["issues"]
    assert "line subscriptions require valid local delivery credentials" in missing["issues"]

    monkeypatch.setenv(lm.SLACK_WEBHOOK_URL_ENV, "https://hooks.slack.com/services/T/B/secret")
    monkeypatch.setenv(lm.DISCORD_WEBHOOK_URL_ENV, "https://discord.com/api/webhooks/123/secret")
    monkeypatch.setenv(lm.LINE_CHANNEL_ACCESS_TOKEN_ENV, "line-access-token")
    monkeypatch.setenv(lm.LINE_TARGET_ID_ENV, "U123")
    assert lm.notification_configuration_status(db_path)["ok"] is True

    user = lm.create_user(db_path, "external@example.com", "external password 1")
    account_watch = lm.add_watch(db_path, "Account External", kind=lm.WATCH_KIND_EVENT, user_id=user.id)
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Account External"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=account_watch.id,
    )
    with pytest.raises(ValueError, match="Account subscriptions cannot use global slack delivery"):
        lm.add_subscription(
            db_path,
            str(account_watch.id),
            lm.NOTIFY_SCOPE_EVENT_ALL,
            channels="feed,slack",
            user_id=user.id,
        )
    # Preserve a runtime defense for a legacy/manually edited database even
    # though current writes reject this unsafe account/global combination.
    with lm.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO notification_subscriptions(
                user_id, watch_id, scope, location, round_key, channels,
                lead_days, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', 'feed,slack', '7,1,0', 1, ?, ?)
            """,
            (
                user.id,
                account_watch.id,
                lm.NOTIFY_SCOPE_EVENT_ALL,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )
    monkeypatch.setattr(
        lm.notifications,
        "send_slack_notification",
        lambda notification: pytest.fail("global webhook must not receive account-scoped reminders"),
    )

    status = lm.notification_configuration_status(db_path)
    assert status["ok"] is False
    assert status["external"]["slack"]["account_subscriptions"] == 1
    assert "account-scoped slack delivery is not configured; use feed or push" in status["issues"]
    delivered = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=user.id)
    assert delivered[0]["delivered"] == {"feed": True, "slack": False}


def test_push_notification_uses_fcm_http_v1_for_each_unique_device(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"name":"projects/configured-project/messages/1"}'

    def fake_open(request):
        requests.append(request)
        return Response()

    monkeypatch.setenv(lm.FCM_PROJECT_ID_ENV, "configured-project")
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("oauth-token", "adc-project"))
    monkeypatch.setattr(lm.notifications, "_open_fcm_request", fake_open)
    notification = {
        "event_id": 42,
        "event_title": "Example Tour",
        "label": "Lottery application opens",
        "subject": "First lottery",
        "location": "Tokyo",
        "field": "application_start_at",
        "date": "2026-10-01",
        "url": "https://official.example/tickets",
        "lead_days": 1,
    }
    devices = [
        lm.DeviceToken(id=1, token="device-one"),
        lm.DeviceToken(id=2, token="device-two"),
        lm.DeviceToken(id=3, token="device-one"),
        lm.DeviceToken(id=4, token=""),
    ]

    assert lm.send_push_notification(notification, devices) is True
    assert len(requests) == 2
    assert {request.full_url for request in requests} == {
        "https://fcm.googleapis.com/v1/projects/configured-project/messages:send"
    }
    assert {request.get_header("Authorization") for request in requests} == {"Bearer oauth-token"}
    payloads = [json.loads(request.data.decode("utf-8")) for request in requests]
    assert {payload["message"]["token"] for payload in payloads} == {"device-one", "device-two"}
    assert all(payload["message"]["data"]["event_id"] == "42" for payload in payloads)
    assert all(isinstance(value, str) for payload in payloads for value in payload["message"]["data"].values())
    assert "registration_ids" not in payloads[0]


def test_push_notification_fails_closed_without_adc_or_project(monkeypatch):
    monkeypatch.delenv(lm.FCM_PROJECT_ID_ENV, raising=False)
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("", ""))
    monkeypatch.setattr(
        lm.notifications,
        "_open_fcm_request",
        lambda request: pytest.fail("FCM must not be contacted without credentials"),
    )

    assert lm.send_push_notification({}, [lm.DeviceToken(id=1, token="device")]) is False


def test_push_notification_attempts_all_devices_and_reports_partial_failure(monkeypatch):
    attempted = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"{}"

    def fake_open(request):
        token = json.loads(request.data.decode("utf-8"))["message"]["token"]
        attempted.append(token)
        if token == "bad-device":
            raise OSError("delivery failed")
        return Response()

    monkeypatch.setenv(lm.FCM_PROJECT_ID_ENV, "configured-project")
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("oauth-token", ""))
    monkeypatch.setattr(lm.notifications, "_open_fcm_request", fake_open)

    assert lm.send_push_notification(
        {"event_title": "Tour", "label": "General sale", "subject": "Tickets", "date": "2026-10-01"},
        [lm.DeviceToken(id=1, token="bad-device"), lm.DeviceToken(id=2, token="good-device")],
    ) is False
    assert attempted == ["bad-device", "good-device"]


def test_unregistered_fcm_token_is_pruned_without_retrying_successful_devices(tmp_path, monkeypatch):
    db_path = str(tmp_path / "unregistered-device.sqlite3")
    watch = lm.add_watch(db_path, "Stale Device", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Stale Device"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=0)
    lm.register_device(db_path, "stale-device", platform="android", user_id=0)
    lm.register_device(db_path, "current-device", platform="ios", user_id=0)
    attempted = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"{}"

    def fake_open(request):
        token = json.loads(request.data.decode("utf-8"))["message"]["token"]
        attempted.append(token)
        if token == "stale-device":
            body = json.dumps(
                {
                    "error": {
                        "code": 404,
                        "status": "NOT_FOUND",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                                "errorCode": "UNREGISTERED",
                            }
                        ],
                    }
                }
            ).encode("utf-8")
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(body))
        return Response()

    monkeypatch.setenv(lm.FCM_PROJECT_ID_ENV, "configured-project")
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("oauth-token", ""))
    monkeypatch.setattr(lm.notifications, "_open_fcm_request", fake_open)

    delivered = lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    assert delivered[0]["delivered"]["push"] is True
    assert set(attempted) == {"stale-device", "current-device"}
    assert [device.token for device in lm.list_devices(db_path, user_id=0)] == ["current-device"]
    assert lm.run_notifications(db_path, now="2026-06-16T01:00:00+00:00", user_id=0) == []
    assert attempted.count("current-device") == 1


def test_fcm_redirects_are_rejected():
    handler = lm.notifications._NoFcmRedirects()
    request = urllib.request.Request("https://fcm.googleapis.com/start")

    assert handler.redirect_request(request, None, 307, "redirect", {}, "https://example.com/steal") is None


def test_anonymous_notification_run_respects_local_muted_watches(tmp_path):
    db_path = str(tmp_path / "anonymous-muted-notifications.sqlite3")
    watch = lm.add_watch(db_path, "Anon Muted", kind=lm.WATCH_KIND_EVENT, user_id=0, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Anon Muted"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed", user_id=0)

    assert lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00", user_id=0)
    assert lm.set_watch_muted(db_path, str(watch.id), True, user_id=0) is True
    assert lm.pending_notifications(db_path, now="2026-06-29T00:00:00+00:00", user_id=0) == []
    assert lm.run_notifications(db_path, now="2026-06-29T00:00:00+00:00", user_id=0) == []


def test_event_location_subscription_filters_rounds_by_city(tmp_path):
    db_path = tmp_path / "n.sqlite3"
    watch = lm.add_watch(str(db_path), "Tour", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    blocks = _subscription_event_blocks(
        "Tour",
        venues=("EXシアター有明", "梅田芸術劇場メインホール"),
        rounds=(
            lm.TicketRound(source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
                           name="抽選先行", general_sale_date="2026-06-23", evidence="東京 EXシアター有明 抽選先行"),
            lm.TicketRound(source="pia", platform="pia", url="https://w.pia.jp/t/x",
                           name="一般発売", general_sale_date="2026-06-23", evidence="大阪 梅田芸術劇場 一般発売"),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    lm.add_subscription(str(db_path), "Tour", lm.NOTIFY_SCOPE_EVENT_LOCATION, location="東京", channels="feed")

    delivered = lm.run_notifications(str(db_path), now="2026-06-16T00:00:00+00:00")
    locations = {n["location"] for n in delivered if n["field"] == "general_sale_date"}
    assert locations == {"東京"}


def test_round_subscription_targets_a_single_round(tmp_path):
    db_path = tmp_path / "n.sqlite3"
    watch = lm.add_watch(str(db_path), "One", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    blocks = _subscription_event_blocks(
        "One",
        rounds=(
            lm.TicketRound(source="pia", platform="pia", url="https://t.pia.jp/a", name="第1次抽選先行", lottery_start="2026-06-23"),
            lm.TicketRound(source="eplus", platform="eplus", url="https://eplus.jp/b", name="第2次抽選先行", lottery_start="2026-06-23"),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    event = lm.recent_events(str(db_path))[0]
    target = next(r for r in event["rounds"] if r["name"] == "第2次抽選先行")
    lm.add_subscription(str(db_path), "One", lm.NOTIFY_SCOPE_ROUND, round_key=target["round_key"], channels="feed")

    delivered = lm.run_notifications(str(db_path), now="2026-06-16T00:00:00+00:00")
    assert {n["subject"] for n in delivered} == {"第2次抽選先行"}


def test_notify_cli_and_device_registration(tmp_path, capsys):
    db_path = tmp_path / "n.sqlite3"
    watch = lm.add_watch(str(db_path), "CLI", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(str(db_path), _subscription_event_blocks("CLI"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)

    assert lm.main(["notify", "subscribe", "CLI", "--scope", "event_all", "--channels", "feed,push", "--db", str(db_path)]) == 0
    assert "Subscribed" in capsys.readouterr().out
    assert lm.main(["notify", "device", "add", "device-token-xyz", "--platform", "android", "--db", str(db_path)]) == 0
    assert "Registered device" in capsys.readouterr().out
    assert lm.main(["notify", "list", "--json", "--db", str(db_path)]) == 0
    assert '"scope": "event_all"' in capsys.readouterr().out


def test_notify_cli_reports_retryable_external_delivery_failure(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "notify-cli-failure.sqlite3")
    watch = lm.add_watch(db_path, "CLI Failure", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    due_date = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=7)).isoformat()
    lm.save_blocks(
        db_path,
        _subscription_event_blocks(
            "CLI Failure",
            rounds=(
                lm.TicketRound(
                    source="official",
                    url="https://official.example/cli-failure",
                    name="CLI failure test",
                    lottery_start=due_date,
                ),
            ),
        ),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    lm.add_subscription(db_path, "CLI Failure", lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push")
    lm.register_device(db_path, "cli-device", platform="android", user_id=0)
    attempts = []

    def fake_push(notification, devices, invalid_tokens=None):
        attempts.append([device.token for device in devices])
        return len(attempts) > 1

    monkeypatch.setattr(lm.notifications, "send_push_notification", fake_push)

    assert lm.main(["notify", "run", "--db", db_path]) == 1
    assert "1 external delivery failure(s) remain eligible for retry" in capsys.readouterr().out

    assert lm.main(["notify", "run", "--db", db_path]) == 0
    assert "Delivered 1 reminder(s)." in capsys.readouterr().out
    assert attempts == [["cli-device"], ["cli-device"]]


def test_notification_status_checks_credentials_and_per_owner_devices(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notification-status.sqlite3")
    monkeypatch.setattr(
        lm.notifications,
        "_fcm_access_token",
        lambda: pytest.fail("ADC should not be checked without push subscriptions"),
    )
    assert lm.notification_configuration_status(db_path)["ok"] is True

    watch = lm.add_watch(db_path, "Status", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=0)
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("", ""))

    missing = lm.notification_configuration_status(db_path)
    assert missing["ok"] is False
    assert missing["push"] == {
        "subscriptions": 1,
        "owner_count": 1,
        "owners_without_devices": 1,
        "credentials_ready": False,
        "project_id_available": False,
    }

    lm.register_device(db_path, "status-device", platform="ios", user_id=0)
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("oauth-token", "firebase-project"))
    ready = lm.notification_configuration_status(db_path)
    assert ready["ok"] is True
    assert ready["push"]["owners_without_devices"] == 0
    assert ready["push"]["credentials_ready"] is True


def test_notify_status_cli_returns_nonzero_for_missing_delivery_configuration(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "notification-status-cli.sqlite3")
    watch = lm.add_watch(db_path, "Status CLI", kind=lm.WATCH_KIND_EVENT, user_id=0)
    lm.add_subscription(db_path, str(watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=0)
    monkeypatch.setattr(lm.notifications, "_fcm_access_token", lambda: ("", ""))

    assert lm.main(["notify", "status", "--db", db_path, "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "FCM ADC credentials or Firebase project id are unavailable" in payload["issues"]


def test_watch_loop_delivers_reminders_each_run(capsys):
    calls = []

    def fake_notify(db_path):
        calls.append(db_path)
        return [{"title": "reminder"}]

    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_EVENT,
        max_runs=1,
        run_func=lambda db_path, kind=None: [],
        sleep_func=lambda seconds: None,
        notify_func=fake_notify,
    ) == 0

    assert calls == ["loop.sqlite3"]
    assert "1 reminders sent." in capsys.readouterr().out


def test_event_run_cli_invokes_reminder_delivery(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "n.sqlite3"
    lm.add_watch(str(db_path), "RunSub", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(lm, "run_watches", lambda db, kind=None: [])
    seen = {}

    def fake_notify(db):
        seen["db"] = db
        return [{"title": "x"}]

    monkeypatch.setattr(lm, "run_notifications", fake_notify)

    assert lm.main(["event", "run", "--db", str(db_path)]) == 0

    assert seen.get("db") == str(db_path)
    assert "1 reminders sent." in capsys.readouterr().out


def test_event_run_cli_exits_nonzero_when_external_delivery_needs_retry(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "event-run-delivery-failure.sqlite3"
    lm.add_watch(str(db_path), "Run Failure", kind=lm.WATCH_KIND_EVENT)
    monkeypatch.setattr(lm, "run_watches", lambda db, kind=None: [])
    monkeypatch.setattr(
        lm,
        "run_notifications",
        lambda db: [{"delivered": {"feed": True, "push": False}}],
    )

    assert lm.main(["event", "run", "--db", str(db_path)]) == 1
    assert "1 awaiting external delivery retry" in capsys.readouterr().out


def test_web_api_registers_device_and_serves_notifications(tmp_path):
    db_path = tmp_path / "n.sqlite3"
    watch = lm.add_watch(str(db_path), "Web", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(str(db_path), _subscription_event_blocks("Web"), now="2026-06-01T00:00:00+00:00", watch_id=watch.id)
    lm.add_subscription(str(db_path), "Web", lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed")
    lm.run_notifications(str(db_path), now="2026-06-16T00:00:00+00:00")

    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        register = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(f"{base}/api/devices", data=b"token=abc123&platform=ios", method="POST"),
                timeout=5,
            ).read().decode("utf-8")
        )
        assert register["platform"] == "ios"
        feed = json.loads(urllib.request.urlopen(f"{base}/api/notifications", timeout=5).read().decode("utf-8"))
        assert any(item["label"] == "Lottery application opens" for item in feed)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_serves_public_privacy_and_support_pages(tmp_path):
    server = lm.create_web_server(str(tmp_path / "public-pages.sqlite3"), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/privacy", timeout=5) as response:
            privacy = response.read().decode("utf-8")
            assert response.headers.get_content_type() == "text/html"
        with urllib.request.urlopen(f"{base}/support", timeout=5) as response:
            support = response.read().decode("utf-8")
            assert response.headers.get_content_type() == "text/html"

        assert "Privacy Policy" in privacy
        assert "does not sell personal information" in privacy
        assert "Firebase device token" in privacy
        assert 'href="/support"' in privacy
        assert "Chusennote Support" in support
        assert "https://chusennote.onrender.com" in support
        assert "github.com/otterlymavis/chusennote/issues/new" in support
        assert 'href="/privacy"' in support
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_ui_subscribe_buttons_and_notifications_page(tmp_path):
    db_path = tmp_path / "ui.sqlite3"
    watch = lm.add_watch(str(db_path), "UITour", kind=lm.WATCH_KIND_EVENT, now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(
        str(db_path),
        _subscription_event_blocks("UITour", venues=("EXシアター有明", "梅田芸術劇場メインホール")),
        now="2026-06-01T00:00:00+00:00",
        watch_id=watch.id,
    )
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert "Notify me" in detail
        assert 'value="event_location"' in detail
        assert 'value="round"' in detail
        urllib.request.urlopen(
            urllib.request.Request(
                f"{base}/subscribe", data=b"watch=1&scope=event_all&channels=feed&redirect=/events/1", method="POST"
            ),
            timeout=5,
        )
        subs = json.loads(urllib.request.urlopen(f"{base}/api/subscriptions", timeout=5).read().decode("utf-8"))
        assert any(subscription["scope"] == "event_all" for subscription in subs)
        page = urllib.request.urlopen(f"{base}/notifications", timeout=5).read().decode("utf-8")
        assert "Subscriptions" in page
        assert "Event — all locations" in page
        assert 'name="channel_slack"' in page
        assert 'name="channel_discord"' in page
        assert 'name="channel_line"' in page
        assert 'name="lead_days" value="7,1,0"' in page

        urllib.request.urlopen(
            urllib.request.Request(
                f"{base}/subscribe",
                data=urllib.parse.urlencode(
                    {
                        "channel_editor": "1",
                        "channel_feed": "1",
                        "channel_slack": "1",
                        "channel_line": "1",
                        "watch": "1",
                        "scope": "event_all",
                        "lead_days": "14,2",
                        "redirect": "/notifications",
                    }
                ).encode("utf-8"),
                method="POST",
            ),
            timeout=5,
        )
        edited = json.loads(urllib.request.urlopen(f"{base}/api/subscriptions", timeout=5).read().decode("utf-8"))[0]
        assert edited["channels"] == "feed,slack,line"
        assert edited["lead_days"] == "14,2"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_account_notification_editor_only_offers_private_channels(tmp_path):
    db_path = str(tmp_path / "account-notification-editor.sqlite3")
    user = lm.create_user(db_path, "editor@example.com", "editor password 1")
    watch = lm.add_watch(db_path, "Private Editor", kind=lm.WATCH_KIND_EVENT, user_id=user.id)
    lm.add_subscription(
        db_path,
        str(watch.id),
        lm.NOTIFY_SCOPE_EVENT_ALL,
        channels="feed,push",
        user_id=user.id,
    )

    page = lm.render_notifications_page(db_path, user_id=user.id)

    assert 'name="channel_push"' in page
    assert 'name="channel_slack"' not in page
    assert 'name="channel_discord"' not in page
    assert 'name="channel_line"' not in page
    assert "Account subscriptions support private feed and push delivery." in page


def test_clear_performance_window_rounds_nulls_show_run_dates():
    # tv-asahi prints the show's run beside a lottery label; it must not be kept
    # as the application window, while a genuine lottery window is preserved.
    performance = lm.TicketRound(
        source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
        name="抽選先行", lottery_start="2026-07-25", lottery_end="2026-08-23",
        evidence="抽選先行 2026年7月25日〜8月23日 EX THEATER",
    )
    sale_from_performance = lm.TicketRound(
        source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
        name="一般発売", general_sale_date="2026-07-25",
        evidence="一般発売 2026年7月25日〜8月23日 EX THEATER",
    )
    isolated_performance = lm.TicketRound(
        source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
        name="抽選先行", lottery_start="2026-07-25",
        evidence="抽選先行 7月25日(土)12:00 EX THEATER （東京都） 受付終了",
    )
    genuine_sale = lm.TicketRound(
        source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
        name="追加公演・一般発売", general_sale_date="2026-07-28",
        evidence="追加公演・一般発売 7月28日 2026年7月25日〜8月23日 EX THEATER",
    )
    genuine_application = lm.TicketRound(
        source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
        name="抽選先行", lottery_start="2026-06-01", lottery_end="2026-06-05",
        evidence="受付期間 2026年6月1日〜6月5日 公演 2026年7月25日〜8月23日 EX THEATER",
    )
    genuine = lm.TicketRound(
        source="horipro", platform="horipro-stage.jp", url="https://horipro-stage.jp/x",
        name="抽選先行", lottery_start="2026-02-28", lottery_end="2026-03-15",
    )
    event_dates = ("2026年7月25日(土)～8月23日(日)",)

    cleared = lm.clear_performance_window_rounds(
        (performance, sale_from_performance, isolated_performance, genuine_sale, genuine_application, genuine),
        event_dates,
    )

    assert cleared[0].lottery_start is None and cleared[0].lottery_end is None
    renormalized = lm.normalize_ticket_round(cleared[0])
    assert renormalized.application_start_at is None and renormalized.application_end_at is None
    assert cleared[1].general_sale_date is None
    assert cleared[2].lottery_start is None
    assert "7月25日" not in cleared[2].evidence
    assert cleared[3].general_sale_date == "2026-07-28"
    assert cleared[4].lottery_start == "2026-06-01" and cleared[4].lottery_end == "2026-06-05"
    assert "2026年6月1日〜6月5日" in cleared[4].evidence
    assert "2026年7月25日〜8月23日" not in cleared[4].evidence
    assert cleared[5].lottery_start == "2026-02-28" and cleared[5].lottery_end == "2026-03-15"


def test_performance_listing_cleanup_is_platform_and_event_agnostic():
    platforms = (
        ("pia", "https://w.pia.jp/t/other/"),
        ("eplus", "https://eplus.jp/other/"),
        ("lawson", "https://l-tike.com/other/"),
        ("rakuten", "https://ticket.rakuten.co.jp/other/"),
        ("ticketboard", "https://ticket.tickebo.jp/other/"),
        ("cnplayguide", "https://www.cnplayguide.com/other/"),
        ("tv-asahi-ticket", "https://ticket.tv-asahi.co.jp/ex/project/other"),
    )
    rounds = tuple(
        lm.TicketRound(
            source=platform,
            platform=platform,
            url=url,
            name="プレリザーブ",
            lottery_start="2026-10-02",
            lottery_end="2026-10-18",
            evidence="プレリザーブ 2026/10/2(金) ～ 2026/10/18(日) Example Hall ( 神奈川県 ) 受付終了",
        )
        for platform, url in platforms
    )

    cleared = lm.clear_performance_window_rounds(rounds, ())

    assert len(cleared) == len(platforms)
    assert all(round_.lottery_start is None and round_.lottery_end is None for round_ in cleared)
    assert all(round_.application_start_at is None and round_.application_end_at is None for round_ in cleared)


def test_performance_listing_cleanup_preserves_explicit_application_windows_on_all_platforms():
    evidence = (
        "抽選先行 受付期間 2026/9/1(火) ～ 2026/9/5(土) "
        "公演 2026/10/2(金) ～ 2026/10/18(日) Example Hall ( 神奈川県 )"
    )
    ticket = lm.TicketRound(
        source="lawson",
        platform="lawson",
        url="https://l-tike.com/other/",
        name="抽選先行",
        lottery_start="2026-09-01",
        lottery_end="2026-09-05",
        evidence=evidence,
    )

    cleared = lm.clear_performance_window_rounds((ticket,), ("2026年10月2日～10月18日",))[0]

    assert (cleared.lottery_start, cleared.lottery_end) == ("2026-09-01", "2026-09-05")
    assert "2026/9/1(火) ～ 2026/9/5(土)" in cleared.evidence
    assert "2026/10/2(金) ～ 2026/10/18(日)" not in cleared.evidence


def test_performance_listing_cleanup_keeps_sale_date_stated_outside_show_range():
    ticket = lm.TicketRound(
        source="pia",
        platform="pia",
        url="https://w.pia.jp/t/other/",
        name="一般発売",
        general_sale_date="2026-09-10",
        evidence="一般発売 2026/9/10(木)10:00 公演 2026/10/2(金) ～ 2026/10/18(日) Example Hall",
    )

    cleared = lm.clear_performance_window_rounds((ticket,), ())[0]

    assert cleared.general_sale_date == "2026-09-10"
    assert "2026/10/2(金) ～ 2026/10/18(日)" not in cleared.evidence


def test_extract_ticket_rounds_names_seat_selection_presale():
    text = "先着 座席選択先行受付 受付期間:2026/3/14(土)10:00～2026/3/17(火)23:59 受付終了"
    page = lm.Page("https://eplus.jp/example/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert any(round_.name == "座席選択先行受付" for round_ in rounds)
    assert all(not round_.name.startswith("Lottery round") for round_ in rounds)


def test_extract_ticket_rounds_drops_legal_text_without_application_signal():
    # Terms-of-service prose mentions 抽選販売 and a date but is not a real round.
    text = (
        "第4条：(利用料金・支払い) お客様は、抽選販売サービスの利用によりチケットを購入した場合には、"
        "各種手数料を購入時に支払うものとします。決済の完了をもって契約が成立します。 2026年1月1日 改定"
    )
    page = lm.Page("https://ticket.tv-asahi.co.jp/ex/project/example", "Terms", text, ())

    assert lm.extract_ticket_rounds(page) == ()


def test_save_blocks_prunes_vanished_round_within_same_platform(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    base = example_blocks("Example").general_info
    first = lm.AppBlocks(
        general_info=base,
        ticket_info=(
            lm.TicketRound(source="pia", platform="pia", url="https://t.pia.jp/example", name="第1次抽選先行", lottery_start="2026-06-10", lottery_end="2026-06-18"),
            lm.TicketRound(source="pia", platform="pia", url="https://t.pia.jp/example", name="第2次抽選先行", lottery_start="2026-07-10", lottery_end="2026-07-18"),
        ),
    )
    lm.save_blocks(str(db_path), first, now="2026-06-01T00:00:00+00:00")

    # The second round disappears from the source on the next pass.
    second = lm.AppBlocks(general_info=base, ticket_info=(first.ticket_info[0],))
    lm.save_blocks(str(db_path), second, now="2026-06-02T00:00:00+00:00")

    with sqlite3.connect(db_path) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM ticket_rounds ORDER BY name")]

    assert names == ["第1次抽選先行"]


def test_save_blocks_keeps_rounds_for_platform_absent_from_new_save(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    base = example_blocks("Example").general_info
    first = lm.AppBlocks(
        general_info=base,
        ticket_info=(
            lm.TicketRound(source="pia", platform="pia", url="https://t.pia.jp/example", name="第1次抽選先行", lottery_start="2026-06-10", lottery_end="2026-06-18"),
            lm.TicketRound(source="eplus", platform="eplus", url="https://eplus.jp/example", name="一般発売", general_sale_date="2026-07-04"),
        ),
    )
    lm.save_blocks(str(db_path), first, now="2026-06-01T00:00:00+00:00")

    # A transient eplus fetch failure yields no eplus rounds; its data must survive.
    second = lm.AppBlocks(general_info=base, ticket_info=(first.ticket_info[0],))
    lm.save_blocks(str(db_path), second, now="2026-06-02T00:00:00+00:00")

    with sqlite3.connect(db_path) as connection:
        platforms = sorted(row[0] for row in connection.execute("SELECT platform FROM ticket_rounds"))

    assert platforms == ["eplus", "pia"]


def test_db_cleanup_removes_legacy_fetch_failed_rounds(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-04T00:00:00+00:00")
    with sqlite3.connect(db_path) as connection:
        event_id = connection.execute("SELECT id FROM events LIMIT 1").fetchone()[0]
        connection.execute(
            """
            INSERT INTO ticket_rounds(event_id, round_key, source, url, name, confidence, status, round_type, membership_required, created_at, updated_at)
            VALUES (?, 'legacy-ff', 'eplus', 'https://eplus.jp/dead/', 'Fetch failed', 50, 'unknown', 'unknown', 'unknown', '2026-06-03', '2026-06-03')
            """,
            (event_id,),
        )

    assert lm.main(["db", "cleanup", "--db", str(db_path), "--json"]) == 0
    counts = json.loads(capsys.readouterr().out)

    assert counts["ticket_rounds"] >= 1
    with sqlite3.connect(db_path) as connection:
        remaining = connection.execute("SELECT COUNT(*) FROM ticket_rounds WHERE name = 'Fetch failed'").fetchone()[0]
    assert remaining == 0


def test_fetch_ticket_link_rounds_skips_unreachable_links_without_placeholder(monkeypatch):
    links = (lm.Link("ｅ＋", "https://eplus.jp/dead/"), lm.Link("Pia", "https://t.pia.jp/live/"))

    def fake_fetch(url):
        if url == "https://t.pia.jp/live/":
            return lm.parse_page(
                url,
                "<html><body><h2>第1次抽選先行</h2>"
                "<p>受付期間 2026年6月10日 ～ 2026年6月18日</p></body></html>",
            )
        raise OSError("404")

    monkeypatch.setattr(lm.pipeline, "fetch_page", fake_fetch)

    rounds = lm.fetch_ticket_link_rounds(links)

    assert all(round_.name != "Fetch failed" for round_ in rounds)
    assert [round_.name for round_ in rounds] == ["第1次抽選先行"]


def test_browser_fetch_mode_reads_environment(monkeypatch):
    monkeypatch.delenv(lm.BROWSER_FETCH_ENV, raising=False)
    assert lm.browser_fetch_mode() == "off"
    monkeypatch.setenv(lm.BROWSER_FETCH_ENV, "fallback")
    assert lm.browser_fetch_mode() == "fallback"
    monkeypatch.setenv(lm.BROWSER_FETCH_ENV, "always")
    assert lm.browser_fetch_mode() == "always"


def test_fetch_page_uses_plain_http_when_browser_disabled(monkeypatch):
    monkeypatch.delenv(lm.BROWSER_FETCH_ENV, raising=False)
    monkeypatch.setattr(
        lm.netio,
        "request_html",
        lambda url: "<html><head><title>Plain</title></head><body>" + ("x" * 600) + "</body></html>",
    )

    def fail_browser(url):
        raise AssertionError("browser fetch must not run when disabled")

    monkeypatch.setattr(lm.netio, "fetch_page_browser", fail_browser)

    assert lm.fetch_page("https://example.test/").title == "Plain"


def test_fetch_transports_reject_local_targets_and_redirects_before_network():
    with pytest.raises(ValueError, match="public HTTP\\(S\\) URL"):
        lm.request_html("http://127.0.0.1/private")
    with pytest.raises(ValueError, match="public HTTP\\(S\\) URL"):
        lm.request_json("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError, match="public HTTP\\(S\\) URL"):
        lm.netio._browser_render("http://localhost/admin")

    handler = lm.netio._PublicFetchRedirectHandler()
    request = urllib.request.Request("https://official.example/event")
    with pytest.raises(urllib.error.URLError, match="redirect target"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/admin",
        )


def test_fetch_redirects_never_forward_api_credentials_cross_origin():
    handler = lm.netio._PublicFetchRedirectHandler()
    request = urllib.request.Request(
        "https://api.example/search",
        headers={"Authorization": "Bearer secret"},
    )

    with pytest.raises(urllib.error.URLError, match="credentialed redirect target changed origin"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other.example/search",
        )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://api.example/next",
    )
    assert redirected.get_header("Authorization") == "Bearer secret"


def test_fetch_response_body_is_bounded():
    assert lm.netio._read_limited_response(io.BytesIO(b"ok")) == b"ok"
    oversized = io.BytesIO(b"x" * (lm.MAX_FETCH_RESPONSE_BYTES + 1))
    with pytest.raises(OSError, match="Fetch response exceeds"):
        lm.netio._read_limited_response(oversized)


def test_request_json_posts_utf8_json_body(monkeypatch):
    captured = {}

    class Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class Response:
        headers = Headers()

        @staticmethod
        def geturl():
            return "https://api.example/search"

        @staticmethod
        def read(_limit):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    def fake_open(request):
        captured["request"] = request
        return Response()

    monkeypatch.setattr(lm.netio, "_open_public_request", fake_open)

    assert lm.request_json(
        "https://api.example/search",
        {"Authorization": "Bearer secret"},
        json_body={"query": "日本 公演"},
    ) == {"ok": True}

    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data.decode("utf-8")) == {"query": "日本 公演"}


def test_fetch_page_falls_back_to_browser_on_fetch_error(monkeypatch):
    monkeypatch.setenv(lm.BROWSER_FETCH_ENV, "fallback")

    def boom(url):
        raise OSError("blocked")

    rendered = lm.Page("https://example.test/", "Rendered", "rendered text", ())
    monkeypatch.setattr(lm.netio, "request_html", boom)
    monkeypatch.setattr(lm.netio, "fetch_page_browser", lambda url: rendered)

    assert lm.fetch_page("https://example.test/") is rendered


def test_fetch_page_falls_back_to_browser_for_thin_page(monkeypatch):
    monkeypatch.setenv(lm.BROWSER_FETCH_ENV, "fallback")
    monkeypatch.setattr(lm.netio, "request_html", lambda url: "<html><body>thin</body></html>")
    rendered = lm.Page("https://example.test/", "Rendered", "y" * 700, ())
    monkeypatch.setattr(lm.netio, "fetch_page_browser", lambda url: rendered)

    assert lm.fetch_page("https://example.test/") is rendered


def test_fetch_page_falls_back_to_browser_for_empty_state_shell(monkeypatch):
    # A JS shell can clear the length threshold yet still hold no real content,
    # e.g. shiki.jp's static HTML printing "公演スケジュール情報はありません".
    monkeypatch.setenv(lm.BROWSER_FETCH_ENV, "fallback")
    shell = "<html><body>" + ("案内 " * 220) + "現在、公演スケジュール情報はありません。" + "</body></html>"
    monkeypatch.setattr(lm.netio, "request_html", lambda url: shell)
    rendered = lm.Page("https://www.shiki.jp/applause/lionking/ticket_schedule/", "Rendered", "z" * 900, ())
    monkeypatch.setattr(lm.netio, "fetch_page_browser", lambda url: rendered)

    assert len(lm.parse_page("https://www.shiki.jp/", shell).text) >= lm.BROWSER_MIN_TEXT_LENGTH
    assert lm.fetch_page("https://www.shiki.jp/applause/lionking/ticket_schedule/") is rendered


def test_page_needs_browser_flags_empty_state_and_thin_pages():
    assert lm.page_needs_browser(lm.Page("u", "t", "short", ()))
    placeholder = lm.Page("u", "t", "案内 " * 80 + "現在、公演スケジュール情報はありません。", ())
    assert lm.page_needs_browser(placeholder)
    full = lm.Page("u", "t", "受付期間 2026年8月1日〜8月10日 " * 20, ())
    assert not lm.page_needs_browser(full)


def test_fetch_page_browser_retries_after_transient_failure(monkeypatch):
    rendered = lm.Page("https://example.test/", "Rendered", "ok", ())
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cold Chromium launch failed")
        return rendered

    monkeypatch.setattr(lm.netio, "_browser_render", flaky)
    assert lm.fetch_page_browser("https://example.test/") is rendered
    assert calls["n"] == 2


def test_fetch_page_browser_gives_up_as_oserror_after_retries(monkeypatch):
    def always_fail(url):
        raise RuntimeError("browser down")

    monkeypatch.setattr(lm.netio, "_browser_render", always_fail)
    with pytest.raises(OSError):
        lm.fetch_page_browser("https://example.test/")


def test_fetch_page_browser_does_not_retry_missing_playwright(monkeypatch):
    calls = {"n": 0}

    def no_playwright(url):
        calls["n"] += 1
        raise ImportError("no playwright")

    monkeypatch.setattr(lm.netio, "_browser_render", no_playwright)
    with pytest.raises(OSError):
        lm.fetch_page_browser("https://example.test/")
    assert calls["n"] == 1


def test_fetch_page_browser_degrades_to_oserror_without_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        with pytest.raises(OSError):
            lm.fetch_page_browser("https://example.test/")
    else:  # pragma: no cover - depends on optional dependency being installed
        pytest.skip("playwright installed; missing-dependency path not exercised")


def test_render_blocks_has_two_expected_app_blocks():
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )

    rendered = lm.render_blocks(blocks)

    assert "# General event info" in rendered
    assert "# Ticket / lottery info" in rendered
    assert "第1次抽選先行" in rendered


def test_dedupe_ticket_rounds_sorts_newest_first():
    older = lm.TicketRound(
        source="pia",
        url="https://t.pia.jp/older",
        name="第1次抽選先行",
        lottery_start="2026-01-10",
        lottery_end="2026-01-18",
    )
    newer = lm.TicketRound(
        source="eplus",
        url="https://eplus.jp/newer",
        name="一般発売",
        general_sale_date="2026-07-04",
    )
    middle = lm.TicketRound(
        source="lawson",
        url="https://l-tike.com/middle",
        name="第2次抽選先行",
        lottery_start="2026-03-01",
        lottery_end="2026-03-08",
    )

    deduped = lm.dedupe_ticket_rounds((older, newer, middle), today=dt.date(2026, 6, 13))

    assert [round_.url for round_ in deduped] == [
        "https://eplus.jp/newer",
        "https://l-tike.com/middle",
        "https://t.pia.jp/older",
    ]


def test_build_blocks_gathers_rounds_from_official_page_and_ticket_links(monkeypatch):
    keyword = "Example Stage"
    results = [lm.SearchResult("Example Stage Official", "https://official.example/stage", keyword)]
    official_html = """
    <html><head><title>Example Stage Official</title></head><body>
      <h1>Example Stage</h1>
      <p>公演日 2026年8月10日 会場 Example Hall</p>
      <section>
        <h2>第1次抽選先行</h2>
        <p>受付期間 2026年6月10日(水) ～ 2026年6月18日(木)</p>
      </section>
      <a href="https://t.pia.jp/example">ぴあ 一般発売はこちら</a>
    </body></html>
    """
    pia_html = """
    <html><head><title>Pia</title></head><body>
      <section>
        <h2>一般発売</h2>
        <p>発売日 2026/07/04 10:00</p>
      </section>
    </body></html>
    """

    def fake_fetch(url):
        if url == "https://official.example/stage":
            return lm.parse_page(url, official_html)
        if url == "https://t.pia.jp/example":
            return lm.parse_page(url, pia_html)
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(lm.pipeline, "fetch_page", fake_fetch)

    blocks = lm.build_blocks(keyword, search_results=results)

    names = {round_.name for round_ in blocks.ticket_info}
    assert "第1次抽選先行" in names
    assert any(round_.general_sale_date == "2026-07-04" for round_ in blocks.ticket_info)


def test_build_blocks_discovers_matching_same_site_rss_pages(monkeypatch):
    keyword = "Example Stage"
    official_url = "https://official.example/stage"
    article_url = "https://official.example/news/example-stage-ticket"
    base_page = lm.parse_page(
        official_url,
        """
        <html><head><title>Example Stage Official</title>
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        </head><body><h1>Example Stage</h1></body></html>
        """,
    )
    article_page = lm.parse_page(
        article_url,
        """<html><head><title>Example Stage ticket update</title></head><body>
        <h2>第2次抽選先行</h2><p>受付期間 2026年7月1日 ～ 2026年7月8日</p>
        </body></html>""",
    )
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        if url == official_url:
            return base_page
        if url == article_url:
            return article_page
        raise AssertionError(f"unexpected page fetch: {url}")

    def fake_request_html(url, params=None):
        assert url == "https://official.example/feed.xml"
        return f"""<?xml version="1.0"?><rss><channel>
        <item><title>Example Stage ticket update</title><link>{article_url}</link></item>
        <item><title>Example Stage external trap</title><link>https://evil.example/private</link></item>
        </channel></rss>"""

    monkeypatch.setattr(lm.pipeline, "fetch_page", fake_fetch)
    monkeypatch.setattr(lm.pipeline, "request_html", fake_request_html)
    blocks = lm.build_blocks(
        keyword,
        search_results=[lm.SearchResult("Example Stage Official", official_url, keyword)],
    )

    assert any(round_.name == "第2次抽選先行" for round_ in blocks.ticket_info)
    assert article_url in fetched
    assert "https://evil.example/private" not in fetched


def test_render_event_card_does_not_link_keyword_fallback_url():
    rendered = lm.render_event_card(
        {
            "id": 1,
            "title": "Example Tour",
            "status": "watching",
            "official_url": "keyword:Example Tour",
            "updated_at": "2026-06-04T00:00:00+00:00",
            "rounds": [],
        }
    )

    assert 'href="keyword:Example Tour"' not in rendered
    assert "Official page unavailable" in rendered


def test_save_blocks_persists_initial_monitoring_state(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )

    alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    assert {"type": "new_official_page", "event": "Example Tour", "url": "https://official.example/"} in alerts
    assert any(alert["type"] == "new_ticket_link" for alert in alerts)
    assert any(alert["type"] == "new_lottery_round" for alert in alerts)


def test_save_blocks_removes_stale_keyword_fallback_after_official_page(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    fallback = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="",
            title="Example",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(lm.Link("Fallback ticket", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="keyword",
                url="keyword:Example",
                name="Manual reminder",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )
    official = example_blocks("Example")

    lm.save_blocks(str(db_path), fallback, now="2026-06-03T00:00:00+00:00")
    lm.save_blocks(str(db_path), official, now="2026-06-04T00:00:00+00:00")

    with sqlite3.connect(db_path) as connection:
        events = connection.execute("SELECT id, official_url FROM events ORDER BY id").fetchall()
        fallback_children = connection.execute(
            """
            SELECT COUNT(*) FROM ticket_rounds
            WHERE url = 'keyword:Example'
            """
        ).fetchone()[0]

    assert [row[1] for row in events] == ["https://official.example/"]
    assert fallback_children == 0


def test_db_cleanup_cli_removes_stale_fallbacks_and_orphans(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    official = example_blocks("Example")

    lm.save_blocks(str(db_path), official, now="2026-06-04T00:00:00+00:00")
    with sqlite3.connect(db_path) as connection:
        watch_id = connection.execute("SELECT id FROM watched_keywords WHERE keyword = 'Example'").fetchone()[0]
        connection.execute(
            """
            INSERT INTO events(watch_id, canonical_title, official_url, summary, event_dates_json, venues_json, status, event_key, created_at, updated_at)
            VALUES (?, 'Example fallback', 'keyword:Example', '', '[]', '[]', 'watching', 'fallback', '2026-06-03', '2026-06-03')
            """,
            (watch_id,),
        )
        connection.execute(
            """
            INSERT INTO ticket_rounds(event_id, round_key, source, url, name, confidence, status, round_type, membership_required, created_at, updated_at)
            VALUES (999, 'orphan', 'manual', 'keyword:Orphan', 'Orphan', 50, 'unknown', 'unknown', 'unknown', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (999, 'https://orphan.example', 'Orphan', 'orphan.example', 40, 'low_confidence', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (1, 'https://official.example/news/ticket-info', 'Important ticket notice', 'official.example', 60, 'manual_public', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (2, 'https://eplus.jp/example', 'eplus', 'eplus', 90, 'ticket_primary', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO snapshots(event_id, snapshot_hash, payload_json, created_at)
            VALUES (999, 'orphan', '{}', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO alert_log(event_id, alert_key, alert_type, payload_json, created_at)
            VALUES (999, 'orphan', 'orphan', '{}', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO watch_sources(watch_id, url, label, platform, confidence, private_note, muted, created_at, updated_at)
            VALUES (999, 'https://orphan.example', 'Orphan', 'orphan.example', 40, 0, 0, '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            UPDATE events
            SET venues_json = '["現在、公演スケジュール情報はありません。 公演一覧はこちら"]'
            WHERE official_url = 'https://official.example/'
            """
        )

    assert lm.main(["db", "cleanup", "--db", str(db_path), "--json"]) == 0

    output = capsys.readouterr().out
    counts = json.loads(output)
    assert counts["keyword_fallback_events"] == 1
    assert counts["ticket_rounds"] == 1
    assert counts["sources"] == 2
    assert counts["snapshots"] == 1
    assert counts["alert_log"] == 1
    assert counts["watch_sources"] == 1
    assert counts["event_venues"] == 1
    with sqlite3.connect(db_path) as connection:
        event_urls = [row[0] for row in connection.execute("SELECT official_url FROM events ORDER BY id")]
        orphan_rounds = connection.execute("SELECT COUNT(*) FROM ticket_rounds WHERE event_id = 999").fetchone()[0]
        merged_source = connection.execute("SELECT event_id FROM sources WHERE url = 'https://eplus.jp/example'").fetchone()[0]
        venues_json = connection.execute("SELECT venues_json FROM events WHERE official_url = 'https://official.example/'").fetchone()[0]

    assert event_urls == ["https://official.example/"]
    assert orphan_rounds == 0
    assert merged_source == 1
    assert json.loads(venues_json) == []


def test_db_cleanup_backfills_ticket_round_dates_from_evidence(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-04T00:00:00+00:00")
    evidence = (
        "【先着先行】 ゴールド会員：2月28日(土)12:00～3月15日(日)23:59 "
        "レギュラー会員：2月28日(土)13:00～3月15日(日)23:59 【一般発売】 3月18日(水)11:00～"
    )
    with sqlite3.connect(db_path) as connection:
        event_id = connection.execute("SELECT id FROM events LIMIT 1").fetchone()[0]
        connection.execute(
            """
            INSERT INTO ticket_rounds(event_id, round_key, source, url, name, lottery_start, confidence, status, round_type, membership_required, evidence, created_at, updated_at)
            VALUES (?, 'member-presale', 'official', 'https://official.example/', '先着先行', '2026-02-28', 50, 'unknown', 'platform', 'yes', ?, '2026-06-03', '2026-06-03')
            """,
            (event_id, evidence),
        )
        connection.execute(
            """
            INSERT INTO ticket_rounds(event_id, round_key, source, url, name, general_sale_date, confidence, status, round_type, membership_required, evidence, created_at, updated_at)
            VALUES (?, 'general-sale', 'official', 'https://official.example/', '一般発売', '2026-03-18', 50, 'unknown', 'general', 'yes', ?, '2026-06-03', '2026-06-03')
            """,
            (event_id, evidence),
        )

    assert lm.main(["db", "cleanup", "--db", str(db_path), "--json"]) == 0

    counts = json.loads(capsys.readouterr().out)
    assert counts["ticket_round_memberships"] == 2
    with sqlite3.connect(db_path) as connection:
        original_count = connection.execute(
            "SELECT COUNT(*) FROM ticket_rounds WHERE round_key = 'member-presale'"
        ).fetchone()[0]
        general_dates = connection.execute(
            "SELECT application_start_at, application_end_at FROM ticket_rounds WHERE round_key = 'general-sale'"
        ).fetchone()
        membership_rows = connection.execute(
            "SELECT name, application_start_at, application_end_at FROM ticket_rounds WHERE name LIKE '%会員%' ORDER BY name"
        ).fetchall()

    assert original_count == 0
    assert membership_rows == [
        ("先着先行 / ゴールド会員", "2026-02-28", "2026-03-15"),
        ("先着先行 / レギュラー会員", "2026-02-28", "2026-03-15"),
    ]
    assert general_dates == (None, None)

    assert lm.main(["db", "cleanup", "--db", str(db_path), "--json"]) == 0
    second_counts = json.loads(capsys.readouterr().out)
    assert second_counts["ticket_round_memberships"] == 0


def test_save_blocks_emits_alert_when_ticket_dates_change(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    original = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
            ),
        ),
    )
    changed = lm.AppBlocks(
        general_info=original.general_info,
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-20",
            ),
        ),
    )

    lm.save_blocks(str(db_path), original, now="2026-06-03T00:00:00+00:00")
    alerts = lm.save_blocks(str(db_path), changed, now="2026-06-04T00:00:00+00:00")

    assert alerts == [
        {
            "type": "ticket_field_changed",
            "event": "Example Tour",
            "round": "第1次抽選先行",
            "field": "lottery_end",
            "old": "2026-06-18",
            "new": "2026-06-20",
            "url": "https://t.pia.jp/example",
        }
    ]
    persisted = [alert for alert in lm.recent_alerts(str(db_path)) if alert["type"] == "ticket_field_changed"]
    assert len(persisted) == 1
    assert persisted[0]["field"] == "lottery_end"
    assert persisted[0]["old"] == "2026-06-18"
    assert persisted[0]["new"] == "2026-06-20"


def test_save_blocks_persists_discovery_alerts_without_repeating_unchanged_state(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = example_blocks("Example")

    first_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")
    second_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-04T00:00:00+00:00")

    expected_types = {"new_official_page", "new_ticket_link", "new_lottery_round"}
    assert expected_types <= {alert["type"] for alert in first_alerts}
    assert not expected_types & {alert["type"] for alert in second_alerts}
    persisted_types = {alert["type"] for alert in lm.recent_alerts(str(db_path))}
    assert expected_types <= persisted_types


def test_save_blocks_emits_lifecycle_alerts_for_upcoming_dates(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                results_date="2026-06-03",
                general_sale_date="2026-06-04",
                payment_deadline="2026-06-04",
                trade_start_at="2026-06-03",
                trade_end_at="2026-06-05",
            ),
        ),
    )

    alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T09:00:00+00:00")
    alert_types = {alert["type"] for alert in alerts}
    recent_alerts = lm.recent_alerts(str(db_path))

    assert "lottery_opened" in alert_types
    assert "lottery_closing_soon" in alert_types
    assert "results_today" in alert_types
    assert "general_sale_soon" in alert_types
    assert "payment_due_soon" in alert_types
    assert "trade_opened" in alert_types
    assert "trade_closing_soon" in alert_types
    assert recent_alerts[0]["alert_id"] >= 1
    assert recent_alerts[0]["event_id"] >= 1
    assert recent_alerts[0]["alert_type"] == recent_alerts[0]["type"]
    assert recent_alerts[0]["type_label"] == lm.human_alert_type(recent_alerts[0]["type"])
    assert recent_alerts[0]["event_title"] == "Example Tour"
    assert recent_alerts[0]["watch_id"] >= 1
    assert recent_alerts[0]["watch_keyword"] == "Example"
    assert recent_alerts[0]["watch_kind"] == lm.WATCH_KIND_EVENT
    assert recent_alerts[0]["watch_muted"] is False


def test_save_blocks_does_not_repeat_lifecycle_alerts(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                evidence="受付期間 2026年6月3日 ～ 2026年6月5日",
            ),
        ),
    )

    first_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T09:00:00+00:00")
    second_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T10:00:00+00:00")

    assert any(alert["type"] == "lottery_opened" for alert in first_alerts)
    assert not any(alert["type"] in {"lottery_opened", "lottery_closing_soon"} for alert in second_alerts)


def test_init_db_migrates_existing_current_schema(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE watched_keywords (
                id INTEGER PRIMARY KEY,
                keyword TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE ticket_rounds (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                lottery_start TEXT,
                lottery_end TEXT,
                results_date TEXT,
                general_sale_date TEXT,
                payment_deadline TEXT,
                evidence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        lm.init_db(connection)
        watched_columns = lm.table_columns(connection, "watched_keywords")
        event_columns = lm.table_columns(connection, "events")
        round_columns = lm.table_columns(connection, "ticket_rounds")
        source_columns = lm.table_columns(connection, "watch_sources")
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {"tags", "preferred_regions", "preferred_venues", "muted", "last_checked_at"} <= watched_columns
    assert {
        "event_dates_json",
        "venues_json",
        "ticket_rules_json",
        "ticket_prices_json",
        "organizers_json",
        "lineup_json",
    } <= event_columns
    assert {"platform", "application_start_at", "application_end_at", "confidence", "status"} <= round_columns
    assert {"watch_id", "url", "private_note", "muted"} <= source_columns
    assert user_version == lm.DB_SCHEMA_VERSION


def test_notification_claim_migration_preserves_legacy_log_rows(tmp_path):
    db_path = tmp_path / "legacy-notification-log.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE notification_log (
                id INTEGER PRIMARY KEY,
                notification_key TEXT NOT NULL UNIQUE,
                subscription_id INTEGER,
                event_id INTEGER,
                channel TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO notification_log(notification_key, channel, payload_json, created_at) VALUES (?, ?, ?, ?)",
            ("legacy-key", "feed", json.dumps({"title": "Legacy reminder"}), "2026-01-01T00:00:00+00:00"),
        )
        lm.init_db(connection)
        columns = lm.table_columns(connection, "notification_log")
        row = connection.execute(
            "SELECT processing_at, updated_at, attempt_count FROM notification_log WHERE notification_key = ?",
            ("legacy-key",),
        ).fetchone()
        exists, delivery = lm.notification_delivery_status(connection, "legacy-key")

    assert {"processing_at", "updated_at", "attempt_count"} <= columns
    assert row == (None, "2026-01-01T00:00:00+00:00", 0)
    assert exists is True
    assert delivery == {"_legacy_complete": True}


def test_watch_preference_migration_preserves_legacy_user_memberships(tmp_path):
    db_path = str(tmp_path / "legacy-user-watch-preferences.sqlite3")
    user = lm.create_user(db_path, "legacy@example.com", "legacy password 1")
    watch = lm.add_watch(
        db_path,
        "Legacy Tour",
        kind=lm.WATCH_KIND_EVENT,
        tags="legacy-tag",
        preferred_regions="Tokyo",
        preferred_venues="Legacy Hall",
        alert_preferences="results_today",
        user_id=0,
    )
    with lm.connect(db_path) as connection:
        connection.execute("DROP TABLE user_watches")
        connection.execute(
            """
            CREATE TABLE user_watches (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                watch_id INTEGER NOT NULL,
                muted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(user_id, watch_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO user_watches(user_id, watch_id, muted, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
            (user.id, watch.id, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute("PRAGMA user_version = 14")

    migrated = lm.list_watches(db_path, user_id=user.id)[0]
    assert migrated.kind == lm.WATCH_KIND_EVENT
    assert migrated.tags == "legacy-tag"
    assert migrated.preferred_regions == "Tokyo"
    assert migrated.preferred_venues == "Legacy Hall"
    assert migrated.alert_preferences == "results_today"
    with lm.connect(db_path) as connection:
        assert {
            "kind", "tags", "preferred_regions", "preferred_venues", "alert_preferences"
        } <= lm.table_columns(connection, "user_watches")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == lm.DB_SCHEMA_VERSION


def test_round_number_status_and_dedupe_timeline():
    ticket = lm.TicketRound(
        source="pia",
        platform="pia",
        url="https://t.pia.jp/example",
        name="第２次抽選先行",
        lottery_start="2026-06-01",
        lottery_end="2026-06-04",
    )

    normalized = lm.normalize_ticket_round(ticket, today=lm.dt.date(2026, 6, 3))
    deduped = lm.dedupe_ticket_rounds((ticket, ticket), today=lm.dt.date(2026, 6, 3))

    assert normalized.round_number == 2
    assert normalized.application_start_at == "2026-06-01"
    assert normalized.application_end_at == "2026-06-04"
    assert normalized.status == "closing_soon"
    assert len(deduped) == 1


def test_adapter_dispatch_labels_ticket_platform():
    html = """
    <html><body>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    page = lm.parse_page("https://eplus.jp/example", html)

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert rounds[0].platform == "eplus"
    assert rounds[0].source == "eplus"
    assert rounds[0].confidence == 90


def test_pia_adapter_separates_named_rounds_and_reads_provider_deadlines():
    page = lm.Page(
        "https://t.pia.jp/pia/event/example",
        "Ticket",
        (
            "★いち早プレリザーブ ■申込受付期間 "
            "2026年6月29日(月)12:00 ～ 2026年7月5日(日)23:59 "
            "■抽選結果発表 2026年7月9日(木)20:00 "
            "■お支払い期限 2026年7月11日(土)23:59 "
            "★セブン-イレブンWEB抽選先行 ■申込受付期間 "
            "2026年7月12日(日)12:00 ～ 2026年7月18日(土)23:59 "
            "■抽選結果発表 2026年7月22日(水)20:00"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert [(round_.name, round_.application_start_at, round_.application_end_at) for round_ in rounds] == [
        ("セブン-イレブンWEB抽選先行", "2026-07-12", "2026-07-18"),
        ("いち早プレリザーブ", "2026-06-29", "2026-07-05"),
    ]
    first = next(round_ for round_ in rounds if round_.name == "いち早プレリザーブ")
    assert first.results_date == "2026-07-09"
    assert first.payment_end_at == "2026-07-11"
    assert "セブン-イレブンWEB抽選先行" not in first.evidence


def test_eplus_adapter_reads_preorder_result_confirmation_period():
    page = lm.Page(
        "https://eplus.jp/example/",
        "Ticket",
        (
            "プレオーダー（抽選）申込期間 2026年7月25日(土)12:00 ～ 2026年8月2日(日)23:59 "
            "抽選結果確認期間 2026年8月5日(水)13:00 ～ 2026年8月6日(木)18:00"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "プレオーダー（抽選）"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-07-25", "2026-08-02")
    assert rounds[0].results_date == "2026-08-05"


def test_lawson_adapter_reads_prerequest_result_and_store_payment_window():
    page = lm.Page(
        "https://l-tike.com/order/example",
        "Ticket",
        (
            "抽選 プレリク先行 受付期間 2026年8月10日(月)10:00 ～ 2026年8月23日(日)23:59 "
            "抽選結果発表日時：2026年8月26日(水)15:00頃 "
            "店頭入金期間：2026年8月26日(水)15:00 ～ 2026年8月29日(土)23:00"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "プレリク先行"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-08-10", "2026-08-23")
    assert rounds[0].results_date == "2026-08-26"
    assert (rounds[0].payment_start_at, rounds[0].payment_end_at) == ("2026-08-26", "2026-08-29")


def test_cnplayguide_adapter_prefers_application_window_over_performance_dates():
    page = lm.Page(
        "https://www.cnplayguide.com/evt/evtdtl.aspx?ecd=CNI15362",
        "Ticket",
        (
            "奥華子 CONCERT TOUR 2026 2次先行抽選予約 "
            "公演日：2026年10月8日 ～ 2026年10月8日 会場：彩の国さいたま芸術劇場 "
            "抽選予約受付期間：2026年6月26日12:00 ～ 2026年7月12日23:59 "
            "3次先行抽選予約 抽選予約受付期間：2026年7月15日12:00 ～ 2026年7月30日23:59"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert [(round_.name, round_.application_start_at, round_.application_end_at) for round_ in rounds] == [
        ("3次先行抽選予約", "2026-07-15", "2026-07-30"),
        ("2次先行抽選予約", "2026-06-26", "2026-07-12"),
    ]
    assert all("2026-10-08" not in (round_.application_start_at, round_.application_end_at) for round_ in rounds)


def test_rakuten_adapter_reads_reception_and_result_schedule():
    page = lm.Page(
        "https://ticket.rakuten.co.jp/features/example/",
        "Ticket",
        (
            "抽選先行受付 受付日程 受付期間 "
            "2026年8月25日(火)10:00 ～ 2026年8月28日(金)18:00 "
            "結果発表日時 2026年9月4日(金)13:00 "
            "クレジットカードでの決済となります。支払期限：2026年9月7日(月)23:59"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "抽選先行受付"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-08-25", "2026-08-28")
    assert rounds[0].results_date == "2026-09-04"
    assert rounds[0].payment_end_at == "2026-09-07"


def test_ticketboard_adapter_keeps_distinct_named_cards_with_shared_deadline():
    page = lm.Page(
        "https://ticket.tickebo.jp/top/ja/static/example/index.html",
        "2026 CONCERT in TOKYO DOME",
        (
            "2026 CONCERT in TOKYO DOME "
            "ファンクラブ2次先行（抽選）▼ 受付中～10月26日(日)23:59まで "
            "SMTOWN OFFICIAL JAPAN先行（抽選）▼ 受付中～10月26日(日)23:59まで"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert [(round_.name, round_.application_start_at, round_.application_end_at) for round_ in rounds] == [
        ("SMTOWN OFFICIAL JAPAN先行（抽選）", None, "2026-10-26"),
        ("ファンクラブ2次先行（抽選）", None, "2026-10-26"),
    ]


def test_cnplayguide_adapter_keeps_multiple_general_sale_phases():
    page = lm.Page(
        "https://www.cnplayguide.com/evt/evtdtl.aspx?ecd=EXAMPLE",
        "Ticket",
        (
            "CONCERT TOUR 2026 一般発売（後日発券） 8/10(月)10:00〜11/13(金)23:59 "
            "一般発売（即時発券） 11/19(木)10:00〜公演の4日前20:00まで"
        ),
        (),
    )

    general_rounds = [
        round_ for round_ in lm.extract_ticket_rounds_for_page(page) if round_.name == "一般発売"
    ]

    assert [round_.general_sale_date for round_ in general_rounds] == ["2026-11-19", "2026-08-10"]


def test_ticketboard_adapter_reads_named_application_and_result_dates():
    page = lm.Page(
        "https://ticket.tickebo.jp/example",
        "Ticket",
        (
            "先行抽選受付 申込期間 2026年9月1日(火)12:00 ～ 2026年9月7日(月)23:59 "
            "当選発表日 2026年9月10日(木)18:00"
        ),
        (),
    )

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert len(rounds) == 1
    assert rounds[0].name == "先行抽選受付"
    assert (rounds[0].application_start_at, rounds[0].application_end_at) == ("2026-09-01", "2026-09-07")
    assert rounds[0].results_date == "2026-09-10"


def test_adapter_dispatch_covers_additional_ticket_platforms():
    html = """
    <html><body>
      <h2>先行抽選</h2>
      <p>抽選申込期間 2026年6月10日 ～ 2026年6月18日</p>
      <p>結果発表 2026年6月22日</p>
      <p>支払期限 2026年6月25日</p>
      <p>リセール期間 2026年7月1日 ～ 2026年7月3日</p>
    </body></html>
    """
    cases = (
        ("https://ticket.rakuten.co.jp/music/example", "rakuten"),
        ("https://ticket.tickebo.jp/example", "ticketboard"),
        ("https://www.cnplayguide.com/example", "cnplayguide"),
    )

    for url, platform in cases:
        rounds = lm.extract_ticket_rounds_for_page(lm.parse_page(url, html))

        assert len(rounds) == 1
        assert rounds[0].platform == platform
        assert rounds[0].source == platform
        assert rounds[0].application_start_at == "2026-06-10"
        assert rounds[0].application_end_at == "2026-06-18"
        assert rounds[0].results_date == "2026-06-22"
        assert rounds[0].payment_end_at == "2026-06-25"
        assert rounds[0].trade_start_at == "2026-07-01"
        assert rounds[0].trade_end_at == "2026-07-03"


def example_blocks(keyword="Example"):
    return lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword=keyword,
            official_page="https://official.example/",
            title=f"{keyword} Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )


def test_legacy_cli_invocation_still_renders_blocks(monkeypatch, capsys):
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["Example"]) == 0
    output = capsys.readouterr().out

    assert "# General event info" in output
    assert "Example Tour" in output


def test_search_cli_json_outputs_blocks(monkeypatch, capsys):
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["search", "Example", "--json"]) == 0
    output = capsys.readouterr().out

    assert '"keyword": "Example"' in output
    assert '"ticket_info"' in output


def test_session_log_flag_writes_daily_markdown_log(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "history_logs"
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["search", "Example", "--json", "--session-log", "--session-log-dir", str(log_dir)]) == 0
    capsys.readouterr()

    logs = list(log_dir.glob("session_*.md"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "lottery_monitor.py search Example --json --session-log --session-log-dir" in content
    assert "- Target: `search`" in content
    assert "- Exit code: `0`" in content


def test_session_log_args_work_before_legacy_keyword():
    args = lm.parse_args(["--session-log", "--session-log-dir=logs", "Example"])

    assert args.command == "legacy"
    assert args.keyword == "Example"
    assert args.session_log is True
    assert args.session_log_dir == "logs"


def test_session_log_redacts_database_credentials_and_device_tokens(tmp_path):
    database_url = "postgresql://private-user:private-password@db.internal/app?sslmode=require"
    device_token = "private-device-token"
    parsed = lm.parse_args(
        [
            "notify",
            "device",
            "add",
            device_token,
            "--db",
            database_url,
            "--session-log",
            "--session-log-dir",
            str(tmp_path),
        ]
    )

    log_path = lm.append_session_log(
        [
            "notify",
            "device",
            "add",
            device_token,
            "--db",
            database_url,
            "--session-log",
            "--session-log-dir",
            str(tmp_path),
        ],
        parsed,
        0,
        dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 9, 14, 0, 0, 1, tzinfo=dt.timezone.utc),
    )
    content = log_path.read_text()

    assert device_token not in content
    assert "private-user" not in content
    assert "private-password" not in content
    assert "sslmode" not in content
    assert content.count("<redacted-database-url>") == 2
    assert "<redacted-device-token>" in content


def test_session_log_redacts_url_capabilities_but_keeps_plain_urls():
    assert lm.redact_url_for_log("https://example.com/events") == "https://example.com/events"
    assert lm.redact_url_for_log(
        "https://user:password@example.com/events?token=private#section"
    ) == "https://example.com/events?<redacted>"
    assert lm.redact_url_for_log("https://user:password@example.com:not-a-port/") == "<redacted-url>"


def test_watch_add_list_remove_cli(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"

    assert lm.main(["watch", "add", "Example", "--db", str(db_path), "--tags", "musical"]) == 0
    add_output = capsys.readouterr().out
    assert "Added watch 1: Example" in add_output

    assert lm.main(["watch", "list", "--db", str(db_path), "--json"]) == 0
    list_output = capsys.readouterr().out
    assert '"keyword": "Example"' in list_output
    assert '"tags": "musical"' in list_output

    assert lm.main(["watch", "remove", "Example", "--db", str(db_path)]) == 0
    remove_output = capsys.readouterr().out
    assert "Removed watch." in remove_output

    assert lm.main(["watch", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out

    assert lm.main(["watch", "unmute", "Example", "--db", str(db_path)]) == 0
    unmute_output = capsys.readouterr().out
    assert "Unmuted watch." in unmute_output

    assert lm.main(["watch", "list", "--db", str(db_path), "--json"]) == 0
    restored_output = capsys.readouterr().out
    assert '"muted": false' in restored_output

    assert lm.main(["watch", "mute", "Example", "--db", str(db_path)]) == 0
    mute_output = capsys.readouterr().out
    assert "Muted watch." in mute_output

    assert lm.main(["watch", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out

    assert lm.main(["watch", "list", "--db", str(db_path), "--include-muted"]) == 0
    muted_list_output = capsys.readouterr().out
    assert "Example [muted]" in muted_list_output

    assert lm.main(["export", "tracked-events", "--db", str(db_path), "--include-muted"]) == 0
    export_output = capsys.readouterr().out
    assert '"keyword": "Example"' in export_output
    assert '"muted": true' in export_output


def test_watch_identity_is_normalized_and_validated_at_persistence_boundary(tmp_path):
    db_path = str(tmp_path / "watch-validation.sqlite3")

    watch = lm.add_watch(db_path, "  Example\n  Musical  ", kind=lm.WATCH_KIND_EVENT)
    assert watch.keyword == "Example Musical"

    for keyword in ("", "   ", "x" * (lm.MAX_KEYWORD_LENGTH + 1)):
        with pytest.raises(ValueError):
            lm.add_watch(db_path, keyword, kind=lm.WATCH_KIND_EVENT)
    with pytest.raises(ValueError, match="kind must be one of"):
        lm.add_watch(db_path, "Invalid Kind", kind="unknown")

    oversized_blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="x" * (lm.MAX_KEYWORD_LENGTH + 1),
            official_page="",
            title="",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    with pytest.raises(ValueError, match="200 characters or fewer"):
        lm.save_blocks(db_path, oversized_blocks)
    assert [item.keyword for item in lm.list_watches(db_path)] == ["Example Musical"]


def test_source_and_device_inputs_are_normalized_and_bounded_at_persistence_boundary(tmp_path):
    db_path = str(tmp_path / "source-device-validation.sqlite3")
    lm.add_watch(db_path, "Example")

    private = lm.add_watch_source(
        db_path,
        "Example",
        "  members-only lottery opens Friday  ",
        "  Fan\n club note  ",
        private_note=True,
    )
    assert private.url == "members-only lottery opens Friday"
    assert private.label == "Fan club note"

    invalid_public_urls = (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:secret@example.com/ticket",
    )
    for url in invalid_public_urls:
        with pytest.raises(ValueError, match="credential-free public HTTP\\(S\\) URL"):
            lm.add_watch_source(db_path, "Example", url)
    with pytest.raises(ValueError, match="4096 characters or fewer"):
        lm.add_watch_source(db_path, "Example", "x" * (lm.MAX_SOURCE_VALUE_LENGTH + 1), private_note=True)
    with pytest.raises(ValueError, match="120 characters or fewer"):
        lm.add_watch_source(
            db_path,
            "Example",
            "https://official.example/event",
            "x" * (lm.MAX_SOURCE_LABEL_LENGTH + 1),
        )
    assert [source.url for source in lm.list_watch_sources(db_path)] == ["members-only lottery opens Friday"]

    device = lm.register_device(db_path, "  firebase-token  ", platform=" IOS ", label="  My\n phone  ")
    assert (device.token, device.platform, device.label) == ("firebase-token", "ios", "My phone")
    with pytest.raises(ValueError, match="4096 characters or fewer"):
        lm.register_device(db_path, "x" * (lm.MAX_DEVICE_TOKEN_LENGTH + 1))
    with pytest.raises(ValueError, match="one of: android, ios"):
        lm.register_device(db_path, "other-token", platform="web")
    with pytest.raises(ValueError, match="120 characters or fewer"):
        lm.register_device(db_path, "other-token", label="x" * (lm.MAX_DEVICE_LABEL_LENGTH + 1))
    assert [item.token for item in lm.list_devices(db_path)] == ["firebase-token"]


def test_invalid_watch_identity_returns_controlled_cli_and_http_errors(tmp_path, capsys, monkeypatch):
    db_path = str(tmp_path / "invalid-watch-input.sqlite3")
    assert lm.main(["watch", "add", "   ", "--db", db_path]) == 1
    assert capsys.readouterr().out.strip() == "keyword is required"
    assert lm.main(["search", "x" * (lm.MAX_KEYWORD_LENGTH + 1)]) == 1
    assert "200 characters or fewer" in capsys.readouterr().out

    search_called = False

    def fail_search(keyword, limit=6):
        nonlocal search_called
        search_called = True
        return ()

    monkeypatch.setattr(lm.web, "search_web", fail_search)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as oversized_watch:
            post_form(
                f"{base}/api/watchlist",
                {"keyword": "x" * (lm.MAX_KEYWORD_LENGTH + 1), "kind": "event"},
            )
        assert oversized_watch.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as invalid_kind:
            post_form(f"{base}/api/watchlist", {"keyword": "Example", "kind": "unknown"})
        assert invalid_kind.value.code == 400

        oversized_query = urllib.parse.urlencode(
            {"keyword": "x" * (lm.MAX_KEYWORD_LENGTH + 1)}
        )
        with pytest.raises(urllib.error.HTTPError) as oversized_search:
            urllib.request.urlopen(f"{base}/api/event/search?{oversized_query}", timeout=5)
        assert oversized_search.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as private_event_url:
            post_form(
                f"{base}/api/event/add",
                {"keyword": "Internal", "url": "http://127.0.0.1/admin"},
            )
        assert private_event_url.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as unsafe_source:
            post_form(
                f"{base}/api/sources",
                {"watch": "Example", "url": "https://user:secret@example.com/ticket"},
            )
        assert unsafe_source.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as invalid_device:
            post_form(
                f"{base}/api/devices",
                {"token": "browser-token", "platform": "web"},
            )
        assert invalid_device.value.code == 400
        assert search_called is False
        assert lm.list_watches(db_path) == []
        assert lm.list_devices(db_path) == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_kind_watch_mute_unmute_cli(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"

    assert lm.main(["event", "add", "Example Event", "--db", str(db_path)]) == 0
    capsys.readouterr()

    assert lm.main(["event", "mute", "Example Event", "--db", str(db_path)]) == 0
    assert "Muted tracked event." in capsys.readouterr().out

    assert lm.main(["event", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out

    assert lm.main(["event", "unmute", "Example Event", "--db", str(db_path)]) == 0
    assert "Unmuted tracked event." in capsys.readouterr().out

    assert lm.main(["event", "list", "--db", str(db_path), "--json"]) == 0
    assert '"keyword": "Example Event"' in capsys.readouterr().out


def test_watch_run_cli_outputs_alerts_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["watch", "run", "--db", str(db_path), "--alerts-json"]) == 0
    output = capsys.readouterr().out

    assert '"type": "new_official_page"' in output
    assert '"type": "new_lottery_round"' in output


def test_watch_run_continues_after_single_watch_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Broken", now="2026-06-01T00:00:00+00:00")
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")

    def fake_build(db_path_value, watch):
        if watch.keyword == "Broken":
            raise OSError("network failed")
        return example_blocks(watch.keyword)

    monkeypatch.setattr(lm.pipeline, "build_blocks_for_watch", fake_build)

    alerts = lm.run_watches(str(db_path), now="2026-06-03T00:00:00+00:00")
    watches = lm.list_watches(str(db_path))

    assert any(alert["type"] == "watch_failed" and alert["keyword"] == "Broken" for alert in alerts)
    assert any(alert["type"] == "new_official_page" and alert["event"] == "Example Tour" for alert in alerts)
    assert all(watch.last_checked_at == "2026-06-03T00:00:00+00:00" for watch in watches)


def test_watch_loop_runs_selected_kind_multiple_times(capsys):
    calls = []

    def fake_run(db_path, kind=None):
        calls.append((db_path, kind))
        return [{"type": "example"}]

    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_ARTIST,
        max_runs=2,
        run_func=fake_run,
        sleep_func=lambda seconds: None,
    ) == 0

    assert calls == [("loop.sqlite3", lm.WATCH_KIND_ARTIST), ("loop.sqlite3", lm.WATCH_KIND_ARTIST)]
    output = capsys.readouterr().out
    assert "Run 1: checked artist watches; 1 alerts." in output
    assert "Run 2: checked artist watches; 1 alerts." in output


def test_watch_loop_outputs_json_batches(capsys):
    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_EVENT,
        max_runs=1,
        alerts_json=True,
        run_func=lambda db_path, kind=None: [{"type": "example", "kind": kind}],
        sleep_func=lambda seconds: None,
    ) == 0

    output = capsys.readouterr().out
    assert '"run": 1' in output
    assert '"kind": "event"' in output


def test_watch_loop_json_reports_external_delivery_failures(capsys):
    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_EVENT,
        max_runs=1,
        alerts_json=True,
        run_func=lambda db_path, kind=None: [],
        notify_func=lambda db_path: [{"delivered": {"feed": True, "push": False}}],
        sleep_func=lambda seconds: None,
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["reminders"] == 1
    assert payload["reminder_delivery_failures"] == 1


def test_watch_loop_keyboard_interrupt_exits_cleanly(capsys):
    def interrupt(seconds):
        raise KeyboardInterrupt

    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=1,
        run_immediately=False,
        max_runs=1,
        sleep_func=interrupt,
        run_func=lambda db_path, kind=None: [],
    ) == 0

    assert "Watch loop stopped." in capsys.readouterr().out


def test_web_server_keyboard_interrupt_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("CHUSENNOTE_REQUIRE_POSTGRES", raising=False)
    class InterruptingServer:
        server_port = 8877
        closed = False

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    server = InterruptingServer()
    monkeypatch.setattr(lm.web, "create_web_server", lambda db_path, port, host: server)

    lm.web.run_web("example.sqlite3", 8877)

    output = capsys.readouterr().out
    assert server.closed
    assert "Serving chusennote at http://127.0.0.1:8877" in output
    assert "Chusennote server stopped." in output


def test_hosted_web_requires_postgres_before_listening(monkeypatch):
    def unexpected_server(db_path, port, host):
        pytest.fail("server must not listen without PostgreSQL")

    monkeypatch.setenv("CHUSENNOTE_REQUIRE_POSTGRES", "1")
    monkeypatch.delenv("CHUSENNOTE_DATABASE_URL", raising=False)
    monkeypatch.setattr(lm.web, "create_web_server", unexpected_server)

    with pytest.raises(ValueError, match="requires a PostgreSQL"):
        lm.web.run_web(lm.DEFAULT_DB_PATH, 8877)

    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "sqlite:///ephemeral.sqlite3")
    with pytest.raises(ValueError, match="requires a PostgreSQL"):
        lm.web.run_web(lm.DEFAULT_DB_PATH, 8877)

    class InterruptingServer:
        server_port = 8877

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://user@db.example/app")
    monkeypatch.setattr(lm.web, "create_web_server", lambda db_path, port, host: InterruptingServer())
    lm.web.run_web(lm.DEFAULT_DB_PATH, 8877)


def test_watch_loop_argparse_validation():
    for args in (
        ["watch", "loop", "--interval-minutes", "-1"],
        ["watch", "loop", "--max-runs", "0"],
        ["watch", "loop", "--stop-after-errors", "0"],
    ):
        try:
            lm.parse_args(args)
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError(f"expected argparse failure for {args}")


def test_artist_and_event_commands_are_separate_lanes(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(lm.pipeline, "build_artist_blocks", lambda keyword: lm.AppBlocks(example_blocks(keyword).general_info, ()))
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["artist", "add", "Artist Name", "--db", str(db_path)]) == 0
    assert lm.main(["event", "add", "Event Name", "--db", str(db_path)]) == 0
    capsys.readouterr()

    assert lm.main(["artist", "list", "--db", str(db_path), "--json"]) == 0
    artist_output = capsys.readouterr().out
    assert '"keyword": "Artist Name"' in artist_output
    assert '"keyword": "Event Name"' not in artist_output

    assert lm.main(["event", "run", "--db", str(db_path), "--alerts-json"]) == 0
    event_alerts = capsys.readouterr().out
    assert '"type": "new_lottery_round"' in event_alerts

    assert lm.main(["artist", "run", "--db", str(db_path), "--alerts-json"]) == 0
    artist_alerts = capsys.readouterr().out
    assert '"type": "new_lottery_round"' not in artist_alerts


def test_alert_preferences_and_venue_filters_reduce_noise(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(
        str(db_path),
        "Example",
        kind=lm.WATCH_KIND_EVENT,
        preferred_venues="Different Hall",
        alert_preferences="new_lottery_round",
        now="2026-06-01T00:00:00+00:00",
    )
    monkeypatch.setattr(lm, "build_blocks_for_watch", lambda db_path_value, watch: example_blocks(watch.keyword))

    alerts = lm.run_watches(str(db_path), now="2026-06-03T00:00:00+00:00")

    assert alerts == [
        {
            "type": "watch_filtered",
            "watch_id": "1",
            "keyword": "Example",
            "reason": "preferred region/venue did not match",
        }
    ]


def test_source_provenance_and_round_metadata_are_exported(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = example_blocks("Example")

    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")
    events = lm.recent_events(str(db_path))

    assert events[0]["status"] == "lottery_open"
    assert events[0]["status_label"] == "Ticket window open"
    assert events[0]["event_dates"] == ["公演日 2026年7月10日"]
    assert events[0]["venues"] == ["会場 Example Hall"]
    assert any(reason.startswith("keyword match: Example") for reason in events[0]["match_reasons"])
    assert any(reason.startswith("date clue:") for reason in events[0]["match_reasons"])
    assert any(reason.startswith("venue clue:") for reason in events[0]["match_reasons"])
    assert events[0]["rounds"][0]["round_type"] == "platform"
    assert events[0]["rounds"][0]["membership_required"] == "unknown"
    assert "evidence" in events[0]["rounds"][0]


def test_export_cli_outputs_saved_events(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-03T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC", private_note=True)
    assert lm.remove_watch_source(str(db_path), "https://fan.example/private") is True

    assert lm.main(["export", "events", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out

    assert '"title": "Example Tour"' in output
    assert '"status": "lottery_open"' in output
    assert '"event_dates": [' in output
    assert '"match_reasons": [' in output
    assert '"manual_sources": []' in output

    assert lm.main(["export", "events", "--db", str(db_path), "--include-muted"]) == 0
    muted_output = capsys.readouterr().out
    assert '"label": "FC"' in muted_output
    assert '"muted": true' in muted_output

    assert lm.remove_watch(str(db_path), "Example") is True
    assert lm.main(["export", "events", "--db", str(db_path)]) == 0
    muted_watch_output = capsys.readouterr().out
    assert muted_watch_output.strip() == "[]"

    assert lm.main(["export", "events", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_watch_output = capsys.readouterr().out
    assert '"title": "Example Tour"' in included_muted_watch_output


def test_upcoming_export_sorts_urgent_ticket_rounds(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=example_blocks("Closing").general_info,
            ticket_info=(
                lm.TicketRound(
                    source="pia",
                    url="https://t.pia.jp/closing",
                    name="Closing soon",
                    lottery_start="2026-06-01",
                    lottery_end="2026-06-04",
                ),
            ),
        ),
        now="2026-06-03T00:00:00+00:00",
    )
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=example_blocks("Payment").general_info,
            ticket_info=(
                lm.TicketRound(
                    source="eplus",
                    url="https://eplus.jp/payment",
                    name="Payment due",
                    lottery_start="2026-05-01",
                    lottery_end="2026-05-02",
                    payment_deadline="2026-06-04",
                ),
            ),
        ),
        now="2026-06-03T00:00:00+00:00",
    )

    rows = lm.upcoming_priority_rows(str(db_path))

    assert rows[0]["status"] == "closing_soon"
    assert rows[0]["event_title"] == "Closing Tour"
    assert rows[1]["status"] == "payment_due"
    assert rows[1]["relevant_date"] == "2026-06-04"

    assert lm.main(["export", "upcoming", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert '"event_title": "Closing Tour"' in output
    assert '"match_reasons": [' in output

    assert lm.remove_watch(str(db_path), "Closing") is True
    assert lm.remove_watch(str(db_path), "Payment") is True
    assert lm.upcoming_priority_rows(str(db_path)) == []

    muted_rows = lm.upcoming_priority_rows(str(db_path), include_muted_watches=True)
    assert muted_rows[0]["event_title"] == "Closing Tour"

    assert lm.main(["export", "upcoming", "--db", str(db_path)]) == 0
    muted_output = capsys.readouterr().out
    assert muted_output.strip() == "[]"

    assert lm.main(["export", "upcoming", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_output = capsys.readouterr().out
    assert '"event_title": "Closing Tour"' in included_muted_output


def test_upcoming_priority_rows_excludes_closed_rounds(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=lm.EventInfo(
                keyword="Closed",
                official_page="https://official.example/closed",
                title="Closed Tour",
                summary="",
                event_dates=(),
                venues=(),
                ticket_links=(),
            ),
            ticket_info=(
                lm.TicketRound(
                    source="official",
                    url="https://official.example/closed",
                    name="Closed lottery",
                    lottery_start="2026-01-24",
                    lottery_end="2026-02-01",
                ),
            ),
        ),
        now="2026-06-14T00:00:00+00:00",
    )

    assert lm.upcoming_priority_rows(str(db_path)) == []


def test_web_needs_attention_does_not_link_non_web_source_urls(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="manual",
                url="keyword:Example",
                name="Manual reminder",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    home = lm.render_web_page(str(db_path))

    assert 'href="keyword:Example"' not in home
    assert "Tracked Artists" in home
    assert "Tracked Events" in home
    assert "Source unavailable" not in home


def test_event_detail_groups_lottery_rounds_by_ticket_website(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Event",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                platform="pia",
                url="https://w.pia.jp/t/example/",
                name="先着先行 / ゴールド会員",
                lottery_start="2026-02-28",
                lottery_end="2026-03-15",
            ),
            lm.TicketRound(
                source="eplus",
                platform="eplus",
                url="https://eplus.jp/example/",
                name="プレオーダー / 無料会員",
                lottery_start="2026-03-01",
                lottery_end="2026-03-10",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-04T00:00:00+00:00")

    detail = lm.render_event_detail_page(str(db_path), 1)

    # Rounds are grouped by ticket website, keeping membership-specific names.
    assert '<div class="round-group-head">' in detail
    assert "<h3>pia</h3>" in detail
    assert "<h3>eplus</h3>" in detail
    assert "先着先行 / ゴールド会員" in detail
    assert "プレオーダー / 無料会員" in detail


def test_event_detail_groups_touring_rounds_by_city(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Tour",
            official_page="https://official.example/",
            title="Tour Event",
            summary="",
            event_dates=("2026年7月25日(土)～8月23日(日)", "2026年9月10日(木)～21日(月祝)"),
            venues=("EXシアター有明", "梅田芸術劇場メインホール"),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="tv-asahi", platform="tv-asahi-ticket", url="https://ticket.tv-asahi.co.jp/x",
                name="抽選先行", general_sale_date="2026-07-25", evidence="東京 EXシアター有明 抽選先行",
            ),
            lm.TicketRound(
                source="pia", platform="pia", url="https://w.pia.jp/t/x/",
                name="一般発売", general_sale_date="2026-09-10", evidence="大阪 梅田芸術劇場 一般発売",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-04T00:00:00+00:00")

    detail = lm.render_event_detail_page(str(db_path), 1)

    assert "Locations &amp; Tour Dates" in detail
    assert 'class="tour-list"' in detail
    # Lottery rounds are grouped by ticket website, not simplified by city.
    assert "<h3>pia</h3>" in detail
    assert "<h3>tv-asahi-ticket</h3>" in detail


def test_event_detail_renders_official_resale_dates_and_status(tmp_path):
    db_path = tmp_path / "resale-web.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Resale Web",
            official_page="https://official.example/resale",
            title="Resale Web Event",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
            organizers=("Example Productions",),
            lineup=("Example Lead",),
        ),
        ticket_info=(
            lm.TicketRound(
                source="official",
                platform="official",
                url="https://official.example/resale",
                name="公式リセール",
                trade_start_at="2026-07-01",
                trade_end_at="2026-07-03",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-07-01T00:00:00+00:00")

    detail = lm.render_event_detail_page(str(db_path), 1)

    assert "Ticket Rounds" in detail
    assert "Resale opens" in detail
    assert "2026-07-01" in detail
    assert "Resale closes" in detail
    assert "2026-07-03" in detail
    assert "Resale open" in detail
    assert "Example Productions" in detail
    assert "Example Lead" in detail


def test_tracked_event_display_key_prioritizes_official_pages():
    fallback_watch = lm.Watch(
        id=1,
        keyword="Fallback",
        kind=lm.WATCH_KIND_EVENT,
    )
    official_watch = lm.Watch(
        id=2,
        keyword="Official",
        kind=lm.WATCH_KIND_EVENT,
    )
    fallback_event = {"official_url": "keyword:Fallback", "ticket_links": [{}, {}, {}], "rounds": [], "event_dates": []}
    official_event = {"official_url": "https://official.example/", "ticket_links": [{}], "rounds": [], "event_dates": []}

    ordered = sorted(
        (fallback_watch, official_watch),
        key=lambda watch: lm.tracked_event_display_key(watch, fallback_event if watch.id == 1 else official_event),
    )

    assert [watch.keyword for watch in ordered] == ["Official", "Fallback"]


def test_api_health_reports_database_counts(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Artist", kind=lm.WATCH_KIND_ARTIST)
    lm.add_watch(str(db_path), "Event", kind=lm.WATCH_KIND_EVENT)
    lm.add_watch_source(str(db_path), "Event", "https://t.pia.jp/example", "Pia")
    lm.save_blocks(str(db_path), example_blocks("Event"), now="2026-06-03T00:00:00+00:00")

    health = lm.api_health(str(db_path))

    assert health["app"] == "chusennote"
    assert health["version"] == lm.APP_VERSION
    assert health["build"] == lm.APP_BUILD
    assert health["status"] == "ok"
    assert health["schema_version"] == lm.DB_SCHEMA_VERSION
    assert health["db_path"] == "chusennote.sqlite3"
    assert health["tracked_artists"] == 1
    assert health["tracked_events"] >= 1
    assert health["saved_events"] >= 1
    assert health["manual_sources"] == 1

    assert lm.remove_watch(str(db_path), "Event") is True
    muted_health = lm.api_health(str(db_path))
    assert muted_health["manual_sources"] == 0


def test_database_health_label_never_exposes_connection_details():
    assert lm.database_health_label(
        "postgresql://private-user:private-password@db.internal:5432/app?sslmode=require"
    ) == "postgresql"
    assert lm.database_health_label("/Users/private/app.sqlite3") == "app.sqlite3"
    assert lm.database_health_label(":memory:") == ":memory:"


def test_watch_source_cli_add_list_remove(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")

    assert lm.main(["watch", "source", "add", "Example", "https://fan.example/note", "--db", str(db_path), "--label", "FC note", "--private-note"]) == 0
    assert "Added source 1: FC note" in capsys.readouterr().out

    assert lm.main(["watch", "source", "list", "Example", "--db", str(db_path), "--json"]) == 0
    source_output = capsys.readouterr().out
    assert '"label": "FC note"' in source_output
    assert '"private_note": true' in source_output

    assert lm.main(["watch", "source", "remove", "1", "--db", str(db_path)]) == 0
    assert "Removed source." in capsys.readouterr().out

    assert lm.main(["watch", "source", "list", "Example", "--db", str(db_path), "--include-muted"]) == 0
    assert "FC note [muted]" in capsys.readouterr().out


def test_watch_source_cli_mute_unmute_preserves_source_row(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://t.pia.jp/example", "Pia")

    assert lm.main(["watch", "source", "mute", "1", "--db", str(db_path)]) == 0
    assert "Muted source." in capsys.readouterr().out
    assert lm.list_watch_sources(str(db_path)) == []
    muted_sources = lm.list_watch_sources(str(db_path), include_muted=True)
    assert muted_sources[0].muted is True

    assert lm.main(["watch", "source", "unmute", "1", "--db", str(db_path)]) == 0
    assert "Unmuted source." in capsys.readouterr().out
    assert lm.list_watch_sources(str(db_path))[0].muted is False


def test_export_sources_respects_include_muted(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://t.pia.jp/example", "Pia")
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC private", private_note=True)
    assert lm.remove_watch_source(str(db_path), "https://fan.example/private") is True

    assert lm.main(["export", "sources", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert '"label": "Pia"' in output
    assert "FC private" not in output

    assert lm.main(["export", "sources", "--db", str(db_path), "--include-muted"]) == 0
    muted_output = capsys.readouterr().out
    assert '"label": "FC private"' in muted_output
    assert '"muted": true' in muted_output

    assert lm.remove_watch(str(db_path), "Example") is True
    assert lm.main(["export", "sources", "--db", str(db_path)]) == 0
    muted_watch_output = capsys.readouterr().out
    assert muted_watch_output.strip() == "[]"

    assert lm.main(["export", "sources", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_watch_output = capsys.readouterr().out
    assert '"label": "Pia"' in included_muted_watch_output


def test_private_note_sources_are_not_scraped(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC private", private_note=True)
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    def fail_fetch(url):
        raise AssertionError(f"private source should not be fetched: {url}")

    monkeypatch.setattr(lm, "fetch_page", fail_fetch)

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert any(link.url == "https://fan.example/private" for link in blocks.general_info.ticket_links)


def test_public_manual_source_is_authoritative_and_skips_discovery(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://official.example/stage", "公式", private_note=False)

    def fail_discovery(keyword):
        raise AssertionError("web discovery must be skipped when a public manual source exists")

    monkeypatch.setattr(lm.pipeline, "build_blocks", fail_discovery)
    html = """
    <html><head><title>Example Official</title></head><body>
      <h1>Example</h1>
      <p>公演日 2026年7月10日 会場 Example Hall</p>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: lm.parse_page(url, html))

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.general_info.official_page == "https://official.example/stage"
    assert blocks.general_info.title == "Example Official"
    assert any(round.lottery_end == "2026-06-18" for round in blocks.ticket_info)


def test_public_manual_source_adds_ticket_round(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://l-tike.com/example", "Lawson", private_note=False)
    monkeypatch.setattr(
        lm,
        "build_blocks",
        lambda keyword: lm.AppBlocks(
            general_info=example_blocks(keyword).general_info,
            ticket_info=(),
        ),
    )
    html = """
    <html><body>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: lm.parse_page(url, html))

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.ticket_info[0].platform == "lawson"
    assert blocks.ticket_info[0].application_end_at == "2026-06-18"


def test_public_manual_source_fetches_discovered_ticket_links(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00", kind=lm.WATCH_KIND_EVENT)
    lm.add_watch_source(str(db_path), "Example", "https://official.example/stage", "Official", private_note=False)
    official_html = """
    <html><head><title>Example Official</title></head><body>
      <h1>Example</h1>
      <a href="https://eplus.jp/example/">eplus</a>
      <a href="https://w.pia.jp/t/example/">Pia</a>
    </body></html>
    """
    ticket_html = """
    <html><body>
      <h2>先着先行</h2>
      <p>ゴールド会員：2月28日(土)12:00～3月15日(日)23:59</p>
    </body></html>
    """

    def fake_fetch(url):
        return lm.parse_page(url, ticket_html if "eplus.jp" in url or "w.pia.jp" in url else official_html)

    monkeypatch.setattr(lm.pipeline, "fetch_page", fake_fetch)

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert {round_.platform for round_ in blocks.ticket_info} >= {"eplus", "pia"}
    assert all("ゴールド会員" in round_.name for round_ in blocks.ticket_info if round_.platform in {"eplus", "pia"})


def test_watch_discovery_does_not_refetch_ticket_links_already_in_blocks(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00", kind=lm.WATCH_KIND_EVENT)
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))

    def fail_refetch(links):
        raise AssertionError("ticket links from build_blocks should not be fetched twice")

    monkeypatch.setattr(lm.pipeline, "fetch_ticket_link_rounds", fail_refetch)

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.ticket_info


def test_recent_events_sorts_lottery_rounds_latest_to_oldest(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Event",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(source="pia", platform="pia", url="https://w.pia.jp/t/example/", name="Old", lottery_start="2026-02-01", lottery_end="2026-02-10"),
            lm.TicketRound(source="eplus", platform="eplus", url="https://eplus.jp/example/", name="New", lottery_start="2026-05-01", lottery_end="2026-05-10"),
            lm.TicketRound(source="lawson", platform="lawson", url="https://l-tike.com/example/", name="Middle", lottery_start="2026-03-01", lottery_end="2026-03-10"),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-04T00:00:00+00:00")

    event = lm.recent_events(str(db_path))[0]

    assert [round_["name"] for round_ in event["rounds"]] == ["New", "Middle", "Old"]


def test_ticket_rules_and_prices_round_trip_into_event_sections(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Event",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
            ticket_rules=("※未就学児入場不可",),
            ticket_prices=("S席：14,000円",),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-04T00:00:00+00:00")

    event = lm.recent_events(str(db_path))[0]
    detail = lm.render_event_detail_page(str(db_path), int(event["id"]))

    assert event["ticket_rules"] == ["※未就学児入場不可"]
    assert event["ticket_prices"] == ["S席：14,000円"]
    assert "※未就学児入場不可" in detail
    assert "S席：14,000円" in detail
    assert "Ticket rules not captured yet" not in detail
    assert "Ticket prices not captured yet" not in detail


def test_calendar_export_includes_tracked_event_ticket_dates(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=example_blocks("Example").general_info,
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="First lottery",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                evidence="受付期間 2026年6月3日 ～ 2026年6月5日",
                results_date="2026-06-08",
                general_sale_date="2026-06-20",
                payment_deadline="2026-06-10",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    calendar = lm.render_calendar_ics(str(db_path), generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc))

    assert "BEGIN:VCALENDAR" in calendar
    assert "DTSTAMP:20260603T000000Z" in calendar
    assert "SUMMARY:Lottery application: Example Tour - First lottery" in calendar
    assert "DTSTART;VALUE=DATE:20260603" in calendar
    assert "DTEND;VALUE=DATE:20260606" in calendar
    assert "SUMMARY:Lottery results: Example Tour - First lottery" in calendar
    assert "SUMMARY:Payment due: Example Tour - First lottery" in calendar
    assert "SUMMARY:General sale: Example Tour - First lottery" in calendar
    assert "URL:https://t.pia.jp/example" in calendar

    assert lm.remove_watch(str(db_path), "Example") is True
    active_calendar = lm.render_calendar_ics(str(db_path), generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc))
    muted_calendar = lm.render_calendar_ics(
        str(db_path),
        generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc),
        include_muted_watches=True,
    )
    assert "SUMMARY:Lottery application: Example Tour - First lottery" not in active_calendar
    assert "SUMMARY:Lottery application: Example Tour - First lottery" in muted_calendar

    assert lm.main(["export", "calendar", "--db", str(db_path)]) == 0
    cli_active_calendar = capsys.readouterr().out
    assert "Example Tour - First lottery" not in cli_active_calendar

    assert lm.main(["export", "calendar", "--db", str(db_path), "--include-muted"]) == 0
    cli_muted_calendar = capsys.readouterr().out
    assert "Example Tour - First lottery" in cli_muted_calendar


def test_web_server_serves_home_and_api_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-03T00:00:00+00:00")
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        home = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        health = json_load_url(f"{base}/api/health")
        watchlist = json_load_url(f"{base}/api/watchlist")
        sources = json_load_url(f"{base}/api/sources")
        active_sources = json_load_url(f"{base}/api/sources?include_muted=0")
        events = json_load_url(f"{base}/api/events")
        upcoming = json_load_url(f"{base}/api/upcoming")
        alerts = json_load_url(f"{base}/api/alerts")
        calendar_response = urllib.request.urlopen(f"{base}/calendar.ics", timeout=5)
        calendar = calendar_response.read().decode("utf-8")
        muted_calendar = urllib.request.urlopen(f"{base}/calendar.ics?include_muted=1", timeout=5).read().decode("utf-8")

        assert "chusennote" in home
        assert "Tracked Artists" in home
        assert "Tracked Events" in home
        assert f'title="Server release">v{lm.APP_VERSION} ({lm.APP_BUILD}) · schema {lm.DB_SCHEMA_VERSION}</span>' in home
        assert 'role="tablist"' in home
        assert 'data-tab-target="attention"' in home
        assert 'data-tab-target="artists"' in home
        assert 'data-tab-target="events"' in home
        assert "Search exact event" in home
        assert "Search events" in home
        assert 'href="/events/1"' in home
        assert "Calendar feed" not in home
        assert "Muted Watches" not in home
        assert "Muted Sources" not in home
        assert "Needs Attention" in home
        assert 'title="Ticket status">Closing soon</span>' in home
        assert 'title="Ticket status">closing_soon</span>' not in home
        assert "Rounds 1" in home
        assert "Example Tour" in detail
        assert "Ticket window open" in detail
        assert ">lottery_open</span>" not in detail
        assert "General Info" in detail
        assert "Location" in detail
        assert "Time" in detail
        assert "Venue" in detail
        assert "Ticket Rules" in detail
        assert "Ticket Price" in detail
        assert "Ticket Links" in detail
        assert "Ticket Rounds" in detail
        # Only populated facts render: this round has application dates but no
        # payment date, so that label is omitted rather than shown blank.
        assert "Lottery opens" in detail
        assert "Payment due" not in detail
        assert "event Example" not in home
        assert health["status"] == "ok"
        assert health["tracked_events"] >= 1
        assert watchlist[0]["keyword"] == "Example"
        assert sources == []
        assert active_sources == []
        assert events[0]["title"] == "Example Tour"
        assert "match_reasons" in events[0]
        assert "evidence" in events[0]["rounds"][0]
        assert upcoming[0]["event_title"] == "Example Tour"
        assert alerts
        assert {"new_official_page", "new_ticket_link", "new_lottery_round"} <= {
            alert["type"] for alert in alerts
        }
        assert alerts[0]["alert_id"] >= 1
        assert alerts[0]["event_id"] >= 1
        assert alerts[0]["event_title"] == "Example Tour"
        assert alerts[0]["watch_keyword"] == "Example"
        assert alerts[0]["type_label"] == lm.human_alert_type(alerts[0]["type"])
        assert "text/calendar" in calendar_response.headers["Content-Type"]
        assert "BEGIN:VCALENDAR" in calendar
        assert "Example Tour" in muted_calendar
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_command_parses_explicit_host():
    args = lm.parse_args(["web", "--db", "local.sqlite3", "--port", "0", "--host", "0.0.0.0"])

    assert args.command == "web"
    assert args.db == "local.sqlite3"
    assert args.port == 0
    assert args.host == "0.0.0.0"


def test_web_port_defaults_to_host_environment(monkeypatch):
    monkeypatch.setenv("PORT", "10000")

    args = lm.parse_args(["web"])

    assert args.port == 10000
    assert lm.parse_args(["web", "--port", "9000"]).port == 9000


def test_web_event_search_adds_exact_event_with_detail_link(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(
        lm.web,
        "search_web",
        lambda keyword, limit=8: (
            lm.SearchResult("Example Musical Official", "https://official.example/stage", "official event page"),
        ),
    )
    monkeypatch.setattr(
        lm.web,
        "build_exact_event_blocks",
        lambda keyword, title, url, snippet="": example_blocks("Example Musical"),
    )
    monkeypatch.setattr(
        lm.web,
        "save_blocks",
        lambda db_path, blocks, watch_id=None: lm.save_blocks(
            db_path, blocks, now="2026-06-03T00:00:00+00:00", watch_id=watch_id
        ),
    )
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        search_home = post_text(f"{base}/event/search", {"keyword": "Example Musical"})
        assert "Example Musical Official" in search_home
        assert "https://official.example/stage" in search_home
        assert 'title="Add exact event"' in search_home

        added_home = post_text(
            f"{base}/event/add",
            {
                "keyword": "Example Musical",
                "title": "Example Musical Official",
                "url": "https://official.example/stage",
                "snippet": "official event page",
            },
        )
        assert "Example Musical" in added_home
        assert 'href="/events/1"' in added_home
        assert "Tickets 1" in added_home
        assert "Rounds 1" in added_home
        detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert "Official page" in detail
        assert "https://t.pia.jp/example" in detail
        assert "第1次抽選先行" in detail
        assert "Lottery opens" in detail
        assert "Lottery closes" in detail
        # This round has no payment or general-sale date, so those facts are
        # omitted instead of rendered as blank "unknown" cells.
        assert "Payment due" not in detail
        assert "On sale" not in detail
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_api_event_search_adds_exact_event(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(
        lm.web,
        "search_web",
        lambda keyword, limit=6: (
            lm.SearchResult("Example Musical Official", "https://official.example/stage", "official event page"),
        ),
    )
    monkeypatch.setattr(
        lm.web,
        "build_exact_event_blocks",
        lambda keyword, title, url, snippet="": example_blocks("Example Musical"),
    )
    monkeypatch.setattr(
        lm.web,
        "save_blocks",
        lambda db_path, blocks, watch_id=None: lm.save_blocks(
            db_path, blocks, now="2026-06-03T00:00:00+00:00", watch_id=watch_id
        ),
    )
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        results = json_load_url(f"{base}/api/event/search?keyword=Example%20Musical")
        assert results == [
            {
                "title": "Example Musical Official",
                "url": "https://official.example/stage",
                "snippet": "official event page",
            }
        ]

        added = json.loads(
            post_text(
                f"{base}/api/event/add",
                {
                    "keyword": "Example Musical",
                    "title": "Example Musical Official",
                    "url": "https://official.example/stage",
                    "snippet": "official event page",
                },
            )
        )
        assert added["added"] is True
        events = json_load_url(f"{base}/api/events")
        assert events[0]["title"] == "Example Musical Tour"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_api_event_add_scopes_exact_event_to_authenticated_user(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(
        lm.web,
        "build_exact_event_blocks",
        lambda keyword, title, url, snippet="": example_blocks(keyword),
    )
    monkeypatch.setattr(
        lm.web,
        "save_blocks",
        lambda db_path, blocks, watch_id=None: lm.save_blocks(
            db_path, blocks, now="2026-06-03T00:00:00+00:00", watch_id=watch_id
        ),
    )
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        alice = post_form(
            f"{base}/api/auth/register",
            {"email": "alice@example.com", "password": "alice password 1"},
        )
        bob = post_form(
            f"{base}/api/auth/register",
            {"email": "bob@example.com", "password": "bob password 12"},
        )

        added = json.loads(
            post_text_with_token(
                f"{base}/api/event/add",
                alice["token"],
                {
                    "keyword": "Alice Musical",
                    "title": "Alice Musical Official",
                    "url": "https://official.example/alice-stage",
                    "snippet": "official event page",
                },
            )
        )

        assert added["added"] is True
        assert [watch["keyword"] for watch in _get_with_token(f"{base}/api/watchlist", alice["token"])] == ["Alice Musical"]
        assert _get_with_token(f"{base}/api/watchlist", bob["token"]) == []
        assert json_load_url(f"{base}/api/watchlist") == []
        assert [event["title"] for event in _get_with_token(f"{base}/api/events", alice["token"])] == ["Alice Musical Tour"]
        assert _get_with_token(f"{base}/api/events", bob["token"]) == []
        assert json_load_url(f"{base}/api/events") == []
        assert json_load_url(f"{base}/api/upcoming") == []
        assert json_load_url(f"{base}/api/alerts") == []
        anonymous_calendar = urllib.request.urlopen(f"{base}/calendar.ics", timeout=5).read().decode("utf-8")
        # The calendar feed takes a calendar-only token (minted separately),
        # not the account's full-access bearer token, so a leaked feed URL
        # can't be replayed to mutate the account.
        calendar_token = post_form_with_token(f"{base}/api/calendar/token", {}, alice["token"])["token"]
        token_calendar = urllib.request.urlopen(
            f"{base}/calendar.ics?token={urllib.parse.quote(calendar_token)}", timeout=5
        ).read().decode("utf-8")
        with pytest.raises(urllib.error.HTTPError) as bearer_as_calendar_token:
            urllib.request.urlopen(
                f"{base}/calendar.ics?token={urllib.parse.quote(alice['token'])}", timeout=5
            )
        assert "Alice Musical Tour" not in anonymous_calendar
        assert "Alice Musical Tour" in token_calendar
        assert bearer_as_calendar_token.value.code == 401
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_calendar_token_is_scoped_and_not_a_bearer_token(tmp_path):
    db_path = str(tmp_path / "calendar-token.sqlite3")
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        registered = post_form(
            f"{base}/api/auth/register",
            {"email": "cal@example.com", "password": "correct horse battery"},
        )
        token = registered["token"]
        user_id = registered["user"]["id"]
        watch = lm.add_watch(db_path, "Example", kind=lm.WATCH_KIND_EVENT, user_id=user_id)
        lm.save_blocks(db_path, example_blocks("Example"), watch_id=watch.id, now="2026-06-03T00:00:00+00:00")

        # Minting requires authentication.
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            post_form(f"{base}/api/calendar/token", {})
        assert unauthorized.value.code == 401

        first_token = post_form_with_token(f"{base}/api/calendar/token", {}, token)["token"]
        second_token = post_form_with_token(f"{base}/api/calendar/token", {}, token)["token"]
        assert first_token != second_token

        # A second device's first use must preserve existing subscriptions.
        for calendar_token in (first_token, second_token):
            feed = urllib.request.urlopen(
                f"{base}/calendar.ics?token={urllib.parse.quote(calendar_token)}", timeout=5
            ).read().decode("utf-8")
            assert "Example Tour" in feed

        # Revocation is explicit and affects only the authenticated account.
        other = lm.create_user(db_path, "other-cal@example.com", "correct horse battery")
        other_calendar = lm.issue_calendar_token(db_path, other.id)
        rotated = post_form_with_token(f"{base}/api/calendar/token", {"rotate": "1"}, token)["token"]
        assert lm.user_id_for_calendar_token(db_path, first_token) is None
        assert lm.user_id_for_calendar_token(db_path, second_token) is None
        assert lm.user_id_for_calendar_token(db_path, rotated) == user_id
        assert lm.user_id_for_calendar_token(db_path, other_calendar) == other.id

        for invalid_url in (
            f"{base}/calendar.ics?token={urllib.parse.quote(first_token)}",
            f"{base}/calendar.ics?token={urllib.parse.quote(second_token)}",
            f"{base}/calendar.ics?token=unknown",
            f"{base}/calendar.ics?token=",
            f"{base}/calendar.ics?token={urllib.parse.quote(rotated)}&token=unknown",
        ):
            with pytest.raises(urllib.error.HTTPError) as invalid_calendar:
                urllib.request.urlopen(invalid_url, timeout=5)
            assert invalid_calendar.value.code == 401
            assert json.loads(invalid_calendar.value.read()) == {"error": "unauthorized"}

        assert "BEGIN:VCALENDAR" in urllib.request.urlopen(
            f"{base}/calendar.ics?token={urllib.parse.quote(rotated)}", timeout=5
        ).read().decode("utf-8")

        # A calendar token carries no API privileges: used as a bearer token it
        # is simply unauthenticated, not the account it was minted for.
        with pytest.raises(urllib.error.HTTPError) as bearer_misuse:
            _get_with_token(f"{base}/api/auth/me", rotated)
        assert bearer_misuse.value.code == 401
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_calendar_token_migration_preserves_existing_subscriptions(tmp_path):
    db_path = str(tmp_path / "legacy-calendar.sqlite3")
    user = lm.create_user(db_path, "calendar@example.com", "correct horse battery")
    legacy_token = "existing-calendar-subscription"
    with lm.connect(db_path) as connection:
        connection.execute("DROP TABLE calendar_tokens")
        connection.execute(
            """
            CREATE TABLE calendar_tokens (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            "INSERT INTO calendar_tokens(user_id, token_hash, created_at) VALUES (?, ?, ?)",
            (user.id, lm.token_fingerprint(legacy_token), "2026-08-25T00:00:00+00:00"),
        )
        connection.execute("PRAGMA user_version = 12")

    new_token = lm.issue_calendar_token(db_path, user.id)
    assert lm.user_id_for_calendar_token(db_path, legacy_token) == user.id
    assert lm.user_id_for_calendar_token(db_path, new_token) == user.id
    # A repeated initialization is harmless and never drops either token.
    with lm.connect(db_path) as connection:
        lm.init_db(connection)
        assert connection.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == lm.DB_SCHEMA_VERSION


def test_authenticated_api_watch_and_source_mutations_are_user_scoped(tmp_path):
    db_path = str(tmp_path / "api-owner-mutations.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_token = lm.issue_token(db_path, alice.id)
    bob_token = lm.issue_token(db_path, bob.id)
    watch = lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    lm.add_watch(db_path, "Shared Show", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)

    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        alice_watch = post_form_with_token(
            f"{base}/api/watchlist",
            {
                "keyword": "Shared Show",
                "kind": lm.WATCH_KIND_EVENT,
                "tags": "alice",
                "regions": "Tokyo",
                "venues": "Alice Hall",
                "alerts": "results_today",
            },
            alice_token,
        )
        bob_watch = post_form_with_token(
            f"{base}/api/watchlist",
            {
                "keyword": "Shared Show",
                "kind": lm.WATCH_KIND_EVENT,
                "tags": "bob",
                "regions": "Osaka",
                "venues": "Bob Hall",
                "alerts": "payment_due_soon",
            },
            bob_token,
        )
        assert alice_watch["id"] == bob_watch["id"] == watch.id
        assert _get_with_token(f"{base}/api/watchlist", alice_token)[0]["tags"] == "alice"
        assert _get_with_token(f"{base}/api/watchlist", alice_token)[0]["preferred_regions"] == "Tokyo"
        assert _get_with_token(f"{base}/api/watchlist", bob_token)[0]["tags"] == "bob"
        assert _get_with_token(f"{base}/api/watchlist", bob_token)[0]["preferred_regions"] == "Osaka"

        alice_source = post_form_with_token(
            f"{base}/api/sources",
            {"watch": str(watch.id), "url": "https://fan.example/alice", "label": "Alice FC", "private_note": "1"},
            alice_token,
        )
        bob_source = post_form_with_token(
            f"{base}/api/sources",
            {"watch": str(watch.id), "url": "https://fan.example/bob", "label": "Bob FC", "private_note": "1"},
            bob_token,
        )

        assert [source["label"] for source in _get_with_token(f"{base}/api/sources", alice_token)] == ["Alice FC"]
        assert [source["label"] for source in _get_with_token(f"{base}/api/sources", bob_token)] == ["Bob FC"]
        assert json_load_url(f"{base}/api/sources") == []
        assert post_form_with_token(
            f"{base}/api/sources/remove", {"identifier": str(bob_source["id"])}, alice_token
        ) == {"removed": False}
        assert [source["label"] for source in _get_with_token(f"{base}/api/sources", bob_token)] == ["Bob FC"]
        assert post_form_with_token(
            f"{base}/api/sources/remove", {"identifier": str(alice_source["id"])}, alice_token
        ) == {"removed": True}
        assert _get_with_token(f"{base}/api/sources", alice_token) == []

        assert post_form_with_token(f"{base}/api/watchlist/remove", {"identifier": "Shared Show"}, alice_token) == {
            "removed": True
        }
        assert _get_with_token(f"{base}/api/watchlist", alice_token) == []
        assert [watch["keyword"] for watch in _get_with_token(f"{base}/api/watchlist", bob_token)] == ["Shared Show"]
        assert _get_with_token(f"{base}/api/watchlist", bob_token)[0]["muted"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_anonymous_api_mutations_cannot_touch_authenticated_watches(tmp_path):
    db_path = str(tmp_path / "api-anonymous-owner-boundary.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    alice_token = lm.issue_token(db_path, alice.id)
    alice_watch = lm.add_watch(db_path, "Private Show", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    alice_source = lm.add_watch_source(
        db_path,
        str(alice_watch.id),
        "https://fan.example/alice",
        "Alice FC",
        private_note=True,
        user_id=alice.id,
    )
    alice_sub = lm.add_subscription(db_path, str(alice_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, user_id=alice.id)

    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert post_form(f"{base}/api/watchlist/remove", {"identifier": "Private Show"}) == {"removed": False}
        assert post_form(f"{base}/api/watchlist/mute", {"identifier": "Private Show"}) == {"muted": False}
        assert post_form(f"{base}/api/watchlist/unmute", {"identifier": "Private Show"}) == {"unmuted": False}
        assert post_form(f"{base}/api/sources/remove", {"identifier": str(alice_source.id)}) == {"removed": False}
        assert post_form(f"{base}/api/sources/mute", {"identifier": str(alice_source.id)}) == {"muted": False}
        assert post_form(f"{base}/api/sources/unmute", {"identifier": str(alice_source.id)}) == {"unmuted": False}
        assert post_form(f"{base}/api/subscriptions/remove", {"identifier": str(alice_sub.id)}) == {"removed": False}

        with pytest.raises(urllib.error.HTTPError) as add_existing:
            post_form(f"{base}/api/watchlist", {"keyword": "Private Show", "kind": "event"})
        assert add_existing.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as add_source:
            post_form(
                f"{base}/api/sources",
                {"watch": str(alice_watch.id), "url": "https://fan.example/anon", "label": "Anon"},
            )
        assert add_source.value.code == 400

        with pytest.raises(urllib.error.HTTPError) as add_subscription:
            post_form(f"{base}/api/subscriptions", {"watch": str(alice_watch.id), "scope": lm.NOTIFY_SCOPE_EVENT_ALL})
        assert add_subscription.value.code == 400

        assert [watch["keyword"] for watch in _get_with_token(f"{base}/api/watchlist", alice_token)] == ["Private Show"]
        assert _get_with_token(f"{base}/api/watchlist", alice_token)[0]["muted"] is False
        assert [source["label"] for source in _get_with_token(f"{base}/api/sources", alice_token)] == ["Alice FC"]
        assert [sub["id"] for sub in _get_with_token(f"{base}/api/subscriptions", alice_token)] == [alice_sub.id]
        assert json_load_url(f"{base}/api/watchlist") == []

        anonymous_watch = post_form(f"{base}/api/watchlist", {"keyword": "Anonymous Show", "kind": "event"})
        assert anonymous_watch["keyword"] == "Anonymous Show"
        assert [watch["keyword"] for watch in json_load_url(f"{base}/api/watchlist")] == ["Anonymous Show"]
        assert post_form_with_token(
            f"{base}/api/watchlist", {"keyword": "Anonymous Show", "kind": "event"}, alice_token
        )["keyword"] == "Anonymous Show"
        assert [watch["keyword"] for watch in json_load_url(f"{base}/api/watchlist")] == ["Anonymous Show"]
        assert [watch["keyword"] for watch in _get_with_token(f"{base}/api/watchlist", alice_token)] == [
            "Private Show",
            "Anonymous Show",
        ]
        assert post_form(f"{base}/api/watchlist/remove", {"identifier": "Anonymous Show"}) == {"removed": True}
        assert [watch["keyword"] for watch in _get_with_token(f"{base}/api/watchlist", alice_token)] == [
            "Private Show",
            "Anonymous Show",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_artist_detail_lists_discovered_events_sorted_by_date(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    artist = lm.add_watch(str(db_path), "Example Artist", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    later = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/later",
            title="Later Show",
            summary="",
            event_dates=("公演日 2026年9月20日",),
            venues=("会場 Later Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    earlier = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/earlier",
            title="Earlier Show",
            summary="",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Earlier Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), later, now="2026-06-02T00:00:00+00:00")
    lm.save_blocks(str(db_path), earlier, now="2026-06-03T00:00:00+00:00")

    home = lm.render_web_page(str(db_path))
    detail = lm.render_artist_detail_page(str(db_path), artist.id)

    assert f'href="/artists/{artist.id}"' in home
    assert detail.index("Earlier Show") < detail.index("Later Show")
    assert "Date 2026-07-10" in detail
    assert "Date 2026-09-20" in detail
    assert "Venue 会場 Earlier Hall" in detail
    assert "Tickets 0" in detail
    assert "Rounds 0" in detail


def test_artist_run_saves_multiple_discovered_events_under_artist_watch(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    artist = lm.add_watch(str(db_path), "Example Artist", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    first = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/first",
            title="First Artist Event",
            summary="",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 First Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    second = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/second",
            title="Second Artist Event",
            summary="",
            event_dates=("公演日 2026年8月10日",),
            venues=("会場 Second Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    monkeypatch.setattr(lm.pipeline, "build_artist_event_blocks", lambda keyword: [second, first])

    lm.run_watches(str(db_path), now="2026-06-02T00:00:00+00:00", kind=lm.WATCH_KIND_ARTIST)

    events = lm.recent_events(str(db_path), include_muted_sources=True, include_muted_watches=True)
    artist_events = [event for event in events if event["watch_id"] == artist.id]
    detail = lm.render_artist_detail_page(str(db_path), artist.id)
    assert len(artist_events) == 2
    assert detail.index("First Artist Event") < detail.index("Second Artist Event")


def test_artist_event_blocks_fall_back_to_ticket_portal_searches(monkeypatch):
    monkeypatch.setattr(
        lm.pipeline,
        "search_web",
        lambda keyword, limit=8: [lm.SearchResult("Unrelated article", "https://example.com/noise", "libido sodomie")],
    )
    monkeypatch.setattr(
        lm.pipeline,
        "fetch_page",
        lambda url: lm.Page(url=url, title="Unrelated article", text="libido sodomie menopause", links=()),
    )

    blocks = lm.build_artist_event_blocks("yoasobi")

    assert len(blocks) == 1
    assert blocks[0].general_info.title == "yoasobi ticket search"
    assert [link.label for link in blocks[0].general_info.ticket_links] == [
        "Pia search",
        "eplus search",
        "Lawson Ticket search",
    ]


def test_web_server_add_remove_and_run_actions(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        created_watch = post_form(
            f"{base}/api/watchlist",
            {
                "keyword": "Example",
                "tags": "musical",
                "regions": "",
                "venues": "Example Hall",
                "alerts": "new_lottery_round",
            },
        )
        assert created_watch["keyword"] == "Example"
        assert created_watch["tags"] == "musical"
        assert created_watch["preferred_regions"] == ""
        assert created_watch["preferred_venues"] == "Example Hall"
        assert created_watch["alert_preferences"] == "new_lottery_round"
        assert json_load_url(f"{base}/api/watchlist")[0]["keyword"] == "Example"
        home_with_preferences = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        assert "Example" in home_with_preferences
        assert "not searched yet" in home_with_preferences
        assert "Add or update a keyword watch" in home_with_preferences
        assert "Add or update an artist watch" in home_with_preferences
        assert 'name="regions"' in home_with_preferences
        assert 'name="venues"' in home_with_preferences
        assert 'name="alerts"' in home_with_preferences
        assert 'id="artist-watch-editor"' in home_with_preferences
        assert 'id="event-watch-editor"' in home_with_preferences
        assert 'data-edit-watch data-kind="event" data-keyword="Example" data-tags="musical"' in home_with_preferences
        assert "tags musical | regions none | venues Example Hall | alerts New ticket round" in home_with_preferences

        source = post_form(f"{base}/api/sources", {"watch": "Example", "url": "https://fan.example/private", "label": "FC", "private_note": "1"})
        assert source["private_note"] is True

        run_alerts = post_form(f"{base}/api/run", {})
        assert any(alert["type"] == "new_lottery_round" for alert in run_alerts)
        edited_home = post_text(
            f"{base}/watch/add",
            {
                "keyword": "Example",
                "kind": "event",
                "tags": "stage",
                "regions": "Tokyo",
                "venues": "New Hall",
                "alerts": "results_today",
            },
        )
        assert "tags stage | regions Tokyo | venues New Hall | alerts Results today" in edited_home
        edited_watch = json_load_url(f"{base}/api/watchlist")[0]
        assert edited_watch["tags"] == "stage"
        assert edited_watch["preferred_regions"] == "Tokyo"
        assert edited_watch["preferred_venues"] == "New Hall"
        assert edited_watch["alert_preferences"] == "results_today"
        assert 'data-keyword="Example" data-tags="stage" data-regions="Tokyo" data-venues="New Hall"' in edited_home
        home_with_source = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        detail_with_source = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert '<a href="https://fan.example/private">Open</a>' not in home_with_source
        assert '<a class="action-link" href="https://fan.example/private">Open</a>' in detail_with_source

        removed_source = post_form(f"{base}/api/sources/remove", {"identifier": "1"})
        assert removed_source["removed"] is True
        source_list = json_load_url(f"{base}/api/sources")
        muted_source_list = json_load_url(f"{base}/api/sources?include_muted=1")
        events_without_muted_sources = json_load_url(f"{base}/api/events")
        events_with_muted_sources = json_load_url(f"{base}/api/events?include_muted=1")
        assert source_list == []
        assert muted_source_list[0]["muted"] is True
        assert events_without_muted_sources[0]["manual_sources"] == []
        assert events_with_muted_sources[0]["manual_sources"][0]["muted"] is True

        restored_source_home = post_text(f"{base}/source/unmute", {"identifier": "1"})
        assert "Tracked Artists" in restored_source_home
        assert "Tracked Events" in restored_source_home
        assert "Muted Sources" not in restored_source_home
        assert json_load_url(f"{base}/api/sources")[0]["muted"] is False

        removed = post_form(f"{base}/api/watchlist/remove", {"identifier": "Example"})
        assert removed["removed"] is True
        assert json_load_url(f"{base}/api/watchlist") == []
        assert json_load_url(f"{base}/api/watchlist?include_muted=1")[0]["muted"] is True

        unmuted = post_form(f"{base}/api/watchlist/unmute", {"identifier": "Example"})
        assert unmuted["unmuted"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is False

        muted = post_form(f"{base}/api/watchlist/mute", {"identifier": "Example"})
        assert muted["muted"] is True
        assert json_load_url(f"{base}/api/watchlist") == []
        assert json_load_url(f"{base}/api/watchlist?include_muted=1")[0]["muted"] is True
        assert json_load_url(f"{base}/api/sources") == []
        assert json_load_url(f"{base}/api/sources?include_muted=1")[0]["label"] == "FC"
        assert json_load_url(f"{base}/api/events") == []
        assert json_load_url(f"{base}/api/events?include_muted=1")[0]["title"] == "Example Tour"
        assert json_load_url(f"{base}/api/upcoming") == []
        assert json_load_url(f"{base}/api/upcoming?include_muted=1") == []
        muted_detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert "Example Tour" in muted_detail
        assert '<a class="action-link" href="https://fan.example/private">Open</a>' in muted_detail

        restored_home = post_text(f"{base}/watch/unmute", {"identifier": "Example"})
        assert "Tracked Artists" in restored_home
        assert "Tracked Events" in restored_home
        assert "Muted Watches" not in restored_home
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_watch_edit_attributes_escape_saved_preferences(tmp_path):
    db_path = str(tmp_path / "web-edit-escaping.sqlite3")
    lm.add_watch(
        db_path,
        'Show "quoted" <tour>',
        kind=lm.WATCH_KIND_EVENT,
        tags='stage" data-stolen="yes',
        preferred_regions="Tokyo & Chiba",
        preferred_venues="Hall <One>",
        alert_preferences="results_today",
        user_id=0,
    )

    page = lm.render_web_page(db_path, user_id=0)

    assert 'data-keyword="Show &quot;quoted&quot; &lt;tour&gt;"' in page
    assert 'data-tags="stage&quot; data-stolen=&quot;yes"' in page
    assert 'data-regions="Tokyo &amp; Chiba"' in page
    assert 'data-venues="Hall &lt;One&gt;"' in page
    assert 'data-stolen="yes"' not in page


def json_load_url(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))


def post_form(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))


def post_form_with_token(url, values, token):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))


def post_text(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    return urllib.request.urlopen(request, timeout=5).read().decode("utf-8")


def post_text_with_token(url, token, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    return urllib.request.urlopen(request, timeout=5).read().decode("utf-8")


def assert_web_security_headers(response, *, hsts=False):
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    if hsts:
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    else:
        assert response.headers.get("Strict-Transport-Security") is None


def test_web_responses_apply_security_headers(tmp_path, monkeypatch):
    db_path = str(tmp_path / "security-headers.sqlite3")
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in ("/", "/api/health", "/calendar.ics"):
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
                assert_web_security_headers(response)

        class RejectRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                return None

        logout = urllib.request.Request(f"{base}/account/logout", data=b"", method="POST")
        with pytest.raises(urllib.error.HTTPError) as redirect:
            urllib.request.build_opener(RejectRedirects()).open(logout, timeout=5)
        assert redirect.value.code == 303
        assert_web_security_headers(redirect.value)

        monkeypatch.setenv(lm.web.TRUST_PROXY_HEADERS_ENV, "1")
        proxied = urllib.request.Request(
            f"{base}/api/health",
            headers={"Host": "tickets.example", "X-Forwarded-Proto": "https"},
        )
        with urllib.request.urlopen(proxied, timeout=5) as response:
            assert_web_security_headers(response, hsts=True)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_web_rejects_oversized_and_malformed_form_bodies(tmp_path):
    server = lm.create_web_server(str(tmp_path / "bounded-forms.sqlite3"), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for content_length, expected_status, expected_error in (
            (str(lm.web.FORM_BODY_LIMIT + 1), 413, "form body exceeds"),
            ("not-a-number", 400, "invalid content length"),
            ("-1", 400, "invalid content length"),
        ):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.putrequest("POST", "/api/watchlist")
            connection.putheader("Content-Length", content_length)
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == expected_status
            assert expected_error in payload["error"]
            assert_web_security_headers(response)
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_invalid_bearer_never_falls_back_to_anonymous_workspace(tmp_path):
    db_path = str(tmp_path / "invalid-bearer.sqlite3")
    lm.add_watch(db_path, "Anonymous Watch", kind=lm.WATCH_KIND_EVENT, user_id=0)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert [watch["keyword"] for watch in json_load_url(f"{base}/api/watchlist")] == ["Anonymous Watch"]

        for request in (
            urllib.request.Request(
                f"{base}/api/watchlist", headers={"Authorization": "Bearer expired-token"}
            ),
            urllib.request.Request(
                f"{base}/api/watchlist", headers={"Authorization": "Basic malformed"}
            ),
            urllib.request.Request(
                f"{base}/calendar.ics", headers={"Authorization": "Bearer expired-token"}
            ),
        ):
            with pytest.raises(urllib.error.HTTPError) as unauthorized:
                urllib.request.urlopen(request, timeout=5)
            assert unauthorized.value.code == 401
            assert json.loads(unauthorized.value.read()) == {"error": "unauthorized"}

        with pytest.raises(urllib.error.HTTPError) as mutation:
            post_form_with_token(
                f"{base}/api/watchlist",
                {"keyword": "Must Not Become Anonymous", "kind": "event"},
                "expired-token",
            )
        assert mutation.value.code == 401
        assert [watch.keyword for watch in lm.list_watches(db_path, user_id=0)] == ["Anonymous Watch"]

        with pytest.raises(urllib.error.HTTPError) as anonymous_logout:
            post_form(f"{base}/api/auth/logout", {})
        assert anonymous_logout.value.code == 401

        health = urllib.request.Request(
            f"{base}/api/health", headers={"Authorization": "Bearer expired-token"}
        )
        assert json.loads(urllib.request.urlopen(health, timeout=5).read())["status"] == "ok"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_read_form_rejects_excess_fields_transfer_encoding_and_invalid_utf8():
    class FakeHandler:
        def __init__(self, body, headers):
            self.rfile = io.BytesIO(body)
            self.headers = headers

    many_fields = "&".join(f"field{index}=x" for index in range(lm.web.FORM_FIELD_LIMIT + 1)).encode()
    with pytest.raises(lm.web.FormBodyError, match="exceeds 100 fields"):
        lm.web.read_form(FakeHandler(many_fields, {"Content-Length": str(len(many_fields))}))
    with pytest.raises(lm.web.FormBodyError, match="transfer encoding"):
        lm.web.read_form(FakeHandler(b"", {"Transfer-Encoding": "chunked"}))
    with pytest.raises(lm.web.FormBodyError, match="valid UTF-8"):
        lm.web.read_form(FakeHandler(b"\xff", {"Content-Length": "1"}))
    with pytest.raises(lm.web.FormBodyError, match="shorter than content length"):
        lm.web.read_form(FakeHandler(b"x", {"Content-Length": "2"}))


def test_web_bounds_query_fields_and_read_limits(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bounded-query.sqlite3")
    seen_search_limits = []

    def fake_search(keyword, limit=6):
        seen_search_limits.append(limit)
        return ()

    monkeypatch.setattr(lm.web, "search_web", fake_search)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert json_load_url(f"{base}/api/notifications?limit=1") == []
        assert json_load_url(f"{base}/api/event/search?keyword=Example&limit=20") == []
        assert seen_search_limits == [20]

        invalid_paths = (
            "/api/notifications?limit=0",
            "/api/notifications?limit=501",
            "/api/notifications?limit=all",
            "/api/notifications?limit=1&limit=2",
            "/api/event/search?keyword=Example&limit=-1",
            "/api/event/search?keyword=Example&limit=21",
            "/api/event/search?keyword=Example&limit=all",
            "/api/event/search?keyword=Example&limit=1&limit=2",
            "/api/health?" + "&".join(
                f"field{index}=x" for index in range(lm.web.QUERY_FIELD_LIMIT + 1)
            ),
        )
        for path in invalid_paths:
            with pytest.raises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(f"{base}{path}", timeout=5)
            assert rejected.value.code == 400
            assert json.loads(rejected.value.read())["error"]
            assert_web_security_headers(rejected.value)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _get_with_token(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))


def test_notification_api_scopes_authenticated_feed_subscriptions_and_devices(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notify-api-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_token = lm.issue_token(db_path, alice.id)
    bob_token = lm.issue_token(db_path, bob.id)
    alice_watch = lm.add_watch(db_path, "Shared Notify", kind=lm.WATCH_KIND_EVENT, user_id=alice.id)
    bob_watch = lm.add_watch(db_path, "Shared Notify", kind=lm.WATCH_KIND_EVENT, user_id=bob.id)
    assert alice_watch.id == bob_watch.id
    lm.save_blocks(
        db_path,
        _subscription_event_blocks("Shared Notify"),
        now="2026-06-01T00:00:00+00:00",
        watch_id=alice_watch.id,
    )
    alice_sub = lm.add_subscription(
        db_path, str(alice_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=alice.id
    )
    bob_sub = lm.add_subscription(
        db_path, str(bob_watch.id), lm.NOTIFY_SCOPE_EVENT_ALL, channels="feed,push", user_id=bob.id
    )
    lm.register_device(db_path, "alice-device", platform="ios", user_id=alice.id)
    lm.register_device(db_path, "bob-device", platform="ios", user_id=bob.id)
    sent_tokens = []

    def fake_push(notification, devices, invalid_tokens=None):
        sent_tokens.append((notification["subscription_id"], [device.token for device in devices]))
        return True

    monkeypatch.setattr(lm.notifications, "send_push_notification", fake_push)
    lm.run_notifications(db_path, now="2026-06-16T00:00:00+00:00")
    assert (alice_sub.id, ["alice-device"]) in sent_tokens
    assert (bob_sub.id, ["bob-device"]) in sent_tokens

    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        alice_feed = _get_with_token(f"{base}/api/notifications", alice_token)
        bob_feed = _get_with_token(f"{base}/api/notifications", bob_token)
        assert {item["event_title"] for item in alice_feed} == {"Shared Notify Tour"}
        assert {item["event_title"] for item in bob_feed} == {"Shared Notify Tour"}

        assert [sub["id"] for sub in _get_with_token(f"{base}/api/subscriptions", alice_token)] == [alice_sub.id]
        assert [sub["id"] for sub in _get_with_token(f"{base}/api/subscriptions", bob_token)] == [bob_sub.id]

        post_form(f"{base}/api/devices", {"token": "anon-device", "platform": "ios"})
        assert [device["token"] for device in json_load_url(f"{base}/api/devices")] == ["anon-device"]
        assert [device["token"] for device in _get_with_token(f"{base}/api/devices", alice_token)] == ["alice-device"]
        assert [device["token"] for device in _get_with_token(f"{base}/api/devices", bob_token)] == ["bob-device"]
        post_form(f"{base}/api/devices", {"token": "alice-device", "platform": "ios"})
        assert [device["token"] for device in json_load_url(f"{base}/api/devices")] == ["anon-device"]
        assert [device["token"] for device in _get_with_token(f"{base}/api/devices", alice_token)] == ["alice-device"]
        assert json_load_url(f"{base}/api/subscriptions") == []
        assert json_load_url(f"{base}/api/notifications") == []

        assert post_form_with_token(
            f"{base}/api/subscriptions/remove", {"identifier": str(bob_sub.id)}, alice_token
        ) == {"removed": False}
        assert [sub["id"] for sub in _get_with_token(f"{base}/api/subscriptions", bob_token)] == [bob_sub.id]
        assert post_form(f"{base}/api/subscriptions/remove", {"identifier": str(bob_sub.id)}) == {"removed": False}
        assert [sub["id"] for sub in _get_with_token(f"{base}/api/subscriptions", bob_token)] == [bob_sub.id]
        assert post_form_with_token(
            f"{base}/api/subscriptions/remove", {"identifier": str(alice_sub.id)}, alice_token
        ) == {"removed": True}
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_api_notifications_run_scopes_to_authenticated_user(tmp_path, monkeypatch):
    db_path = str(tmp_path / "notify-api-run-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_token = lm.issue_token(db_path, alice.id)
    bob_token = lm.issue_token(db_path, bob.id)
    seen_user_ids = []

    def fake_run_notifications(db_path, user_id=None):
        seen_user_ids.append(user_id)
        return [{"event_title": f"user {user_id}", "title": "Reminder"}]

    monkeypatch.setattr(lm.web, "run_notifications", fake_run_notifications)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        alice_delivered = post_form_with_token(f"{base}/api/notifications/run", {}, alice_token)
        bob_delivered = post_form_with_token(f"{base}/api/notifications/run", {}, bob_token)
        anonymous_delivered = post_form(f"{base}/api/notifications/run", {})

        assert alice_delivered == [{"event_title": f"user {alice.id}", "title": "Reminder"}]
        assert bob_delivered == [{"event_title": f"user {bob.id}", "title": "Reminder"}]
        assert anonymous_delivered == [{"event_title": "user 0", "title": "Reminder"}]
        assert seen_user_ids == [alice.id, bob.id, 0]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_api_watch_run_scopes_to_authenticated_user(tmp_path, monkeypatch):
    db_path = str(tmp_path / "watch-api-run-scope.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "alice password 1")
    bob = lm.create_user(db_path, "bob@example.com", "bob password 12")
    alice_token = lm.issue_token(db_path, alice.id)
    bob_token = lm.issue_token(db_path, bob.id)
    seen_user_ids = []

    def fake_run_watches(db_path, now=None, kind=None, user_id=None):
        seen_user_ids.append(user_id)
        return [{"type": "new_lottery_round", "keyword": f"user {user_id}"}]

    monkeypatch.setattr(lm.web, "run_watches", fake_run_watches)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        alice_alerts = post_form_with_token(f"{base}/api/run", {}, alice_token)
        bob_alerts = post_form_with_token(f"{base}/api/run", {}, bob_token)
        anonymous_alerts = post_form(f"{base}/api/run", {})

        assert alice_alerts == [{"type": "new_lottery_round", "keyword": f"user {alice.id}"}]
        assert bob_alerts == [{"type": "new_lottery_round", "keyword": f"user {bob.id}"}]
        assert anonymous_alerts == [{"type": "new_lottery_round", "keyword": "user 0"}]
        assert seen_user_ids == [alice.id, bob.id, 0]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_auth_register_login_me_logout(tmp_path):
    db_path = tmp_path / "auth-web.sqlite3"
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        registered = post_form(
            f"{base}/api/auth/register",
            {"email": "Web@Example.com", "password": "correct horse battery"},
        )
        assert registered["user"]["email"] == "web@example.com"
        token = registered["token"]
        assert token and _get_with_token(f"{base}/api/auth/me", token)["email"] == "web@example.com"

        # No token is unauthorized.
        with pytest.raises(urllib.error.HTTPError) as no_token:
            urllib.request.urlopen(f"{base}/api/auth/me", timeout=5)
        assert no_token.value.code == 401

        # Login mints a working token; wrong password is rejected.
        login = post_form(
            f"{base}/api/auth/login",
            {"email": "web@example.com", "password": "correct horse battery"},
        )
        assert _get_with_token(f"{base}/api/auth/me", login["token"])["email"] == "web@example.com"
        with pytest.raises(urllib.error.HTTPError) as bad_login:
            post_form(f"{base}/api/auth/login", {"email": "web@example.com", "password": "wrong"})
        assert bad_login.value.code == 401

        # Logout revokes the original token.
        logout = urllib.request.Request(
            f"{base}/api/auth/logout",
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert json.loads(urllib.request.urlopen(logout, timeout=5).read().decode("utf-8"))["revoked"] is True
        with pytest.raises(urllib.error.HTTPError) as revoked:
            _get_with_token(f"{base}/api/auth/me", token)
        assert revoked.value.code == 401
    finally:
        server.shutdown()


def test_browser_account_cookie_scopes_web_ui_and_logout(tmp_path):
    db_path = str(tmp_path / "browser-account.sqlite3")
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    cookie_jar = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def browser_post(path, values):
        request = urllib.request.Request(
            f"{base}{path}",
            data=urllib.parse.urlencode(values).encode("utf-8"),
            method="POST",
        )
        return browser.open(request, timeout=5)

    try:
        browser_post(
            "/account/register",
            {"email": "Browser@Example.com", "password": "correct horse battery"},
        ).read()
        cookies = list(cookie_jar)
        assert len(cookies) == 1
        session_cookie = cookies[0]
        assert session_cookie.name == lm.web.WEB_SESSION_COOKIE
        assert session_cookie.has_nonstandard_attr("HttpOnly")
        assert session_cookie.get_nonstandard_attr("SameSite") == "Strict"
        assert session_cookie.secure is False
        token = session_cookie.value

        # Browser cookies authorize only HTML routes. API clients must present
        # an explicit bearer token, which keeps a same-site cross-origin caller
        # from replaying the browser session against /api/* endpoints.
        with pytest.raises(urllib.error.HTTPError) as cookie_api_request:
            browser.open(f"{base}/api/auth/me", timeout=5)
        assert cookie_api_request.value.code == 401

        browser_post("/watch/add", {"keyword": "Private Browser Watch", "kind": "event"}).read()
        user = lm.user_for_token(db_path, token)
        assert user is not None
        user_watches = lm.list_watches(db_path, user_id=user.id)
        assert [watch.keyword for watch in user_watches] == ["Private Browser Watch"]
        assert lm.list_watches(db_path, user_id=0) == []
        assert "Private Browser Watch" in browser.open(base, timeout=5).read().decode("utf-8")
        assert "Private Browser Watch" not in urllib.request.urlopen(base, timeout=5).read().decode("utf-8")

        lm.save_blocks(
            db_path,
            lm.AppBlocks(
                general_info=lm.EventInfo(
                    keyword="Private Browser Watch",
                    official_page="https://official.example/private",
                    title="Private Browser Event",
                    summary="Account-scoped event",
                    event_dates=(),
                    venues=(),
                    ticket_links=(),
                ),
                ticket_info=(),
            ),
            watch_id=user_watches[0].id,
        )
        event_id = int(lm.recent_events(db_path, user_id=user.id)[0]["id"])
        assert "Private Browser Event" in browser.open(f"{base}/events/{event_id}", timeout=5).read().decode("utf-8")
        assert "Event not found" in urllib.request.urlopen(f"{base}/events/{event_id}", timeout=5).read().decode("utf-8")

        browser_post("/account/logout", {}).read()
        assert list(cookie_jar) == []
        assert lm.user_for_token(db_path, token) is None
        assert "Private Browser Watch" not in browser.open(base, timeout=5).read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_browser_account_accepts_explicit_trusted_https_proxy_and_sets_secure_cookie(tmp_path, monkeypatch):
    db_path = str(tmp_path / "browser-trusted-proxy.sqlite3")
    monkeypatch.setenv(lm.web.TRUST_PROXY_HEADERS_ENV, "1")
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cookie_jar = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/account/register",
            data=urllib.parse.urlencode(
                {"email": "proxy@example.com", "password": "correct horse battery"}
            ).encode("utf-8"),
            headers={"Host": "tickets.example", "X-Forwarded-Proto": "https"},
            method="POST",
        )
        browser.open(request, timeout=5).read()
        cookies = list(cookie_jar)
        assert len(cookies) == 1
        session_cookie = cookies[0]
        assert session_cookie.name == lm.web.WEB_SESSION_COOKIE
        assert session_cookie.has_nonstandard_attr("HttpOnly")
        assert session_cookie.get_nonstandard_attr("SameSite") == "Strict"
        assert session_cookie.secure is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_browser_account_rejects_password_on_public_cleartext_host(tmp_path):
    db_path = str(tmp_path / "browser-public-http.sqlite3")
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        attempts = (
            ("public.example", "/account/register", "unsafe-browser-one@example.com"),
            ("192.0.2.1", "/account/register", "unsafe-browser-two@example.com"),
            ("public.example", "/api/auth/register", "unsafe-api-one@example.com"),
            ("192.0.2.1", "/api/auth/register", "unsafe-api-two@example.com"),
        )
        for host, path, email in attempts:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}{path}",
                data=urllib.parse.urlencode(
                    {"email": email, "password": "correct horse battery"}
                ).encode("utf-8"),
                headers={"Host": host, "X-Forwarded-Proto": "https"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(request, timeout=5)
            assert rejected.value.code == 400
        for _, _, email in attempts:
            assert lm.verify_user(db_path, email, "correct horse battery") is None
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_browser_forms_reject_cross_origin_requests_and_external_redirects(tmp_path):
    db_path = str(tmp_path / "browser-csrf.sqlite3")
    watch = lm.add_watch(db_path, "Browser CSRF", kind=lm.WATCH_KIND_EVENT, user_id=0)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        cross_origin = urllib.request.Request(
            f"{base}/watch/remove",
            data=urllib.parse.urlencode({"identifier": str(watch.id)}).encode("utf-8"),
            headers={"Origin": "http://127.0.0.1:9999"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(cross_origin, timeout=5)
        assert rejected.value.code == 403
        assert lm.list_watches(db_path, user_id=0)[0].muted is False

        assert lm.web.safe_web_redirect("https://evil.example/steal", "/notifications") == "/notifications"
        assert lm.web.safe_web_redirect("//evil.example/steal", "/notifications") == "/notifications"
        assert lm.web.safe_web_redirect("/events/1", "/notifications") == "/events/1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_logout_detaches_only_the_authenticated_accounts_selected_device(tmp_path):
    db_path = str(tmp_path / "logout-device.sqlite3")
    alice = lm.create_user(db_path, "alice@example.com", "correct horse battery")
    bob = lm.create_user(db_path, "bob@example.com", "correct horse battery")
    alice_token = lm.issue_token(db_path, alice.id)
    other_session = lm.issue_token(db_path, alice.id)
    bob_token = lm.issue_token(db_path, bob.id)
    lm.register_device(db_path, "alice-phone", user_id=alice.id)
    lm.register_device(db_path, "alice-tablet", user_id=alice.id)
    lm.register_device(db_path, "bob-phone", user_id=bob.id)
    server = lm.create_web_server(db_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as anonymous:
            post_form(f"{base}/api/auth/logout", {"device_token": "alice-phone"})
        assert anonymous.value.code == 401
        # A different account cannot remove Alice's registration.
        post_form_with_token(f"{base}/api/auth/logout", {"device_token": "alice-phone"}, bob_token)
        assert {d.token for d in lm.list_devices(db_path, user_id=alice.id)} == {"alice-phone", "alice-tablet"}

        result = post_form_with_token(f"{base}/api/auth/logout", {"device_token": "alice-phone"}, alice_token)
        assert result == {"revoked": True, "device_detached": True}
        assert lm.user_for_token(db_path, alice_token) is None
        assert lm.user_for_token(db_path, other_session).id == alice.id
        assert {d.token for d in lm.list_devices(db_path, user_id=alice.id)} == {"alice-tablet"}
        assert {d.token for d in lm.list_devices(db_path, user_id=bob.id)} == {"bob-phone"}
        post_form(f"{base}/api/devices", {"token": "alice-phone", "platform": "android"})
        assert {d.token for d in lm.list_devices(db_path, user_id=alice.id)} == {"alice-tablet"}
        # Retrying after a lost response is harmless even with revoked auth
        # and a queued anonymous registration from the signed-out app.
        retry = post_form_with_token(f"{base}/api/auth/logout", {"device_token": "alice-phone"}, alice_token)
        assert retry["device_detached"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_official_score_ranks_cjk_official_above_noise():
    keyword = "ミュージカル『ディア・エヴァン・ハンセン』"
    official = lm.SearchResult(
        "ミュージカル『ディア・エヴァン・ハンセン』公式サイト",
        "https://dearevanhansen.jp/",
        "公演情報・チケット抽選先行受付",
    )
    noise = lm.SearchResult("Stars : toute l'actu - Gala", "https://www.gala.fr/", "people")
    gmail = lm.SearchResult("Вход в Gmail", "https://support.google.com/mail", "help")

    assert lm.official_score(official, keyword) > lm.official_score(noise, keyword)
    assert lm.official_score(noise, keyword) == 0
    chosen = lm.choose_official_results([noise, gmail, official], keyword, limit=1)
    assert chosen[0].url == "https://dearevanhansen.jp/"


def test_choose_official_results_drops_unrelated_zero_score_results():
    keyword = "帝国劇場"
    results = [
        lm.SearchResult("Pompes Funèbres Ruffieux & Fils Monuments", "https://pfruffieux.ch/", ""),
        lm.SearchResult("CPU-Z | Softwares | CPUID", "https://www.cpuid.com/softwares/cpu-z.html", ""),
    ]

    assert lm.choose_official_results(results, keyword, limit=3) == []


def test_choose_official_results_requires_keyword_relevance_for_generic_hints():
    keyword = "帝国劇場"
    results = [
        lm.SearchResult("News & Politics - Odysee", "https://odysee.com/$/news", ""),
        lm.SearchResult("Live & TV - ZDF", "https://www.zdf.de/live-tv", ""),
        lm.SearchResult("Official support page", "https://support.microsoft.com/en-us", ""),
        lm.SearchResult("Get started with Google Maps", "https://support.google.com/maps/answer/144349", ""),
    ]

    assert lm.choose_official_results(results, keyword, limit=3) == []


def test_choose_official_results_ignores_incidental_low_overlap():
    result = lm.SearchResult(
        "AWS inaugura Gen AI Loft em São Paulo para impulsionar startups",
        "https://itforum.com.br/noticias/aws-inaugura-gen-ai-loft-em-sao-paulo/",
        "",
    )

    assert lm.choose_official_results([result], "YOASOBI ライブ 東京", limit=3) == []


def test_build_blocks_does_not_fetch_unrelated_zero_score_search_results(monkeypatch):
    keyword = "帝国劇場"
    results = [lm.SearchResult("Blender Italia", "https://www.blender.it/", "")]

    def fail_fetch(url):
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(lm, "fetch_page", fail_fetch)

    blocks = lm.build_blocks(keyword, search_results=results)

    assert blocks.general_info.official_page is None
    assert [link.label for link in blocks.general_info.ticket_links] == [
        "Pia search",
        "eplus search",
        "Lawson Ticket search",
    ]
    assert blocks.ticket_info == ()


def test_build_blocks_rejects_fetched_page_that_does_not_match_keyword(monkeypatch):
    keyword = "YOASOBI ライブ 東京"
    results = [
        lm.SearchResult(
            "YOASOBI official live result",
            "https://health.example/blood-pressure",
            "YOASOBI ライブ 東京",
        )
    ]

    monkeypatch.setattr(
        lm,
        "fetch_page",
        lambda url: lm.Page(
            url=url,
            title="What is Normal Blood Pressure by Age and Gender?",
            text="Health advice, diet, exercise, medical history, and appointments.",
            links=(),
        ),
    )

    blocks = lm.build_blocks(keyword, search_results=results)

    assert blocks.general_info.official_page is None
    assert blocks.ticket_info == ()


def test_keyword_overlap_is_high_for_matching_japanese_and_low_for_unrelated():
    keyword = "ディア・エヴァン・ハンセン"
    assert lm.keyword_overlap(keyword, "ディア・エヴァン・ハンセン 公演") > 0.8
    assert lm.keyword_overlap(keyword, "toute l'actu des stars Gala") == 0.0


def test_keyword_matches_latin_artist_by_name_not_incidental_bigrams():
    assert lm.keyword_matches_text("yoasobi", "YOASOBI official live schedule")
    assert not lm.keyword_matches_text("yoasobi", "Sexualité: libido, sodomie, ménopause")


def test_search_api_disabled_without_env(monkeypatch):
    monkeypatch.delenv(lm.SEARCH_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(lm.SEARCH_API_KEY_ENV, raising=False)
    assert lm.search_api("any keyword") == []


def test_search_api_parses_brave_payload(monkeypatch):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "brave")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "test-key")
    captured = {}

    def fake_request_json(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return {
            "web": {
                "results": [
                    {
                        "title": "公式サイト",
                        "url": "https://official.example/stage",
                        "description": "公演 チケット 抽選",
                    },
                    {"title": "no url"},
                ]
            }
        }

    monkeypatch.setattr(lm.search, "request_json", fake_request_json)
    results = lm.search_api("ディア・エヴァン・ハンセン", limit=5)

    assert "api.search.brave.com" in captured["url"]
    assert captured["headers"]["X-Subscription-Token"] == "test-key"
    assert [r.url for r in results] == ["https://official.example/stage"]
    assert results[0].title == "公式サイト"


def test_search_api_posts_tavily_payload(monkeypatch):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "tavily")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "test-key")
    captured = {}

    def fake_request_json(url, headers=None, *, json_body=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json_body"] = json_body
        return {
            "results": [
                {
                    "title": "公式サイト",
                    "url": "https://official.example/stage",
                    "content": "公演 チケット 抽選",
                },
                {"title": "no url"},
            ]
        }

    monkeypatch.setattr(lm.search, "request_json", fake_request_json)
    results = lm.search_api("ディア・エヴァン・ハンセン", limit=5)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["json_body"] == {
        "query": "ディア・エヴァン・ハンセン 公式 チケット 抽選 先行",
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "country": "japan",
    }
    assert [result.url for result in results] == ["https://official.example/stage"]
    assert results[0].snippet == "公演 チケット 抽選"


def test_search_web_prefers_api_results_over_scraping(monkeypatch):
    monkeypatch.setattr(
        lm.search,
        "search_api",
        lambda keyword, limit=8: [lm.SearchResult("api hit", "https://api.example/", "")],
    )

    def fail_scrape(*args, **kwargs):
        raise AssertionError("HTML scraping should not run when the API returns results")

    monkeypatch.setattr(lm.search, "request_html", fail_scrape)
    results = lm.search_web("any keyword")

    assert [r.url for r in results] == ["https://api.example/"]
