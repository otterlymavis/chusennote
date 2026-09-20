"""Secret-safe release configuration checks for local and hosted deployments."""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import urllib.parse
from collections.abc import Mapping

from .models import APP_BUILD, APP_VERSION


PREFLIGHT_COMPONENTS = (
    "database",
    "discovery",
    "fcm-backend",
    "ios-firebase",
    "ios-signing",
    "android-firebase",
    "android-signing",
    "smtp",
    "slack",
    "discord",
    "line",
)
PRODUCTION_PREFLIGHT_COMPONENTS = (
    "database",
    "discovery",
    "fcm-backend",
    "ios-firebase",
    "ios-signing",
    "android-firebase",
    "android-signing",
)


def _present(environment: Mapping[str, str], name: str) -> bool:
    return bool(environment.get(name, "").strip())


def _explicit_credential_file(environment: Mapping[str, str]) -> bool:
    raw_path = environment.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not raw_path:
        return False
    try:
        payload = json.loads(pathlib.Path(raw_path).expanduser().read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    credential_type = payload.get("type")
    if credential_type == "service_account":
        return all(payload.get(key) for key in ("project_id", "client_email", "private_key"))
    if credential_type == "external_account":
        return all(payload.get(key) for key in ("audience", "subject_token_type", "token_url"))
    return False


def _database_url_ready(environment: Mapping[str, str]) -> bool:
    raw_url = environment.get("CHUSENNOTE_DATABASE_URL", "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except ValueError:
        return False
    return parsed.scheme in {"postgres", "postgresql"} and bool(parsed.path.strip("/"))


def _ios_firebase_status(root: pathlib.Path) -> tuple[bool, str, str | None, str | None]:
    candidates = (
        root / "ios" / "Chusennote" / "GoogleService-Info.plist",
        root / "ios" / "GoogleService-Info.plist",
    )
    present = [path for path in candidates if path.is_file()]
    if not present:
        return False, "GoogleService-Info.plist is absent", None, None
    try:
        with present[0].open("rb") as plist_file:
            payload = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException):
        return False, "GoogleService-Info.plist is malformed", None, None
    valid = (
        isinstance(payload, dict)
        and payload.get("BUNDLE_ID") == "com.chusennote.mobile"
        and all(payload.get(key) for key in ("PROJECT_ID", "GOOGLE_APP_ID", "GCM_SENDER_ID"))
    )
    if not valid:
        return (
            False,
            "GoogleService-Info.plist is incomplete or targets another bundle",
            None,
            None,
        )
    return (
        True,
        "valid Firebase plist for com.chusennote.mobile",
        str(payload["PROJECT_ID"]),
        str(payload["GCM_SENDER_ID"]),
    )


def _android_firebase_status(root: pathlib.Path) -> tuple[bool, str, str | None, str | None]:
    path = root / "android" / "app" / "google-services.json"
    if not path.is_file():
        return False, "android/app/google-services.json is absent", None, None
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "android/app/google-services.json is malformed", None, None
    clients = payload.get("client", []) if isinstance(payload, dict) else []
    matching_clients = [
        client
        for client in clients
        if isinstance(client, dict)
        and isinstance(client.get("client_info"), dict)
        and isinstance(client["client_info"].get("android_client_info"), dict)
        and client["client_info"]["android_client_info"].get("package_name") == "com.chusennote.mobile"
        and client["client_info"].get("mobilesdk_app_id")
    ]
    project_info = payload.get("project_info", {}) if isinstance(payload, dict) else {}
    valid = bool(
        isinstance(project_info, dict)
        and project_info.get("project_id")
        and project_info.get("project_number")
        and matching_clients
    )
    if not valid:
        return (
            False,
            "android/app/google-services.json is incomplete or targets another package",
            None,
            None,
        )
    return (
        True,
        "valid Firebase JSON for com.chusennote.mobile",
        str(project_info["project_id"]),
        str(project_info["project_number"]),
    )


def _ios_signing_status(root: pathlib.Path) -> tuple[bool, str]:
    project = root / "ios" / "Chusennote.xcodeproj" / "project.pbxproj"
    try:
        contents = project.read_text()
    except (OSError, UnicodeError):
        return False, "Xcode project is unreadable"
    release_blocks = re.findall(
        r"buildSettings\s*=\s*\{(.*?)\};\s*name\s*=\s*Release\s*;",
        contents,
        flags=re.DOTALL,
    )
    app_release_blocks = [
        block
        for block in release_blocks
        if re.search(r"\bPRODUCT_BUNDLE_IDENTIFIER\s*=\s*com\.chusennote\.mobile\s*;", block)
    ]
    app_release = "\n".join(app_release_blocks)
    teams = {
        value.strip().strip('"')
        for value in re.findall(r"\bDEVELOPMENT_TEAM\s*=\s*([^;]+);", app_release)
        if value.strip().strip('"') and "$" not in value
    }
    production_push = bool(re.search(r"\bAPS_ENVIRONMENT\s*=\s*production\s*;", app_release))
    if teams and production_push:
        return True, "Apple development team and production push entitlement configured; archive signing remains a machine check"
    missing = []
    if not teams:
        missing.append("Apple development team")
    if not production_push:
        missing.append("production push entitlement")
    if not app_release_blocks:
        return False, "missing iOS app Release build configuration"
    return False, f"missing {' and '.join(missing)}"


def _webhook_ready(environment: Mapping[str, str], name: str, hosts: set[str], path_prefix: str) -> bool:
    raw_url = environment.get(name, "").strip()
    if not raw_url:
        return False
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in hosts
        and parsed.path.startswith(path_prefix)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _release_metadata_status(root: pathlib.Path) -> dict[str, object]:
    try:
        android = (root / "android" / "app" / "build.gradle").read_text()
        ios = (root / "ios" / "Chusennote.xcodeproj" / "project.pbxproj").read_text()
    except (OSError, UnicodeError):
        return {"ready": False, "version": None, "build": None, "detail": "mobile release metadata is unreadable"}
    android_version = re.search(r'\bversionName\s+["\']([^"\']+)["\']', android)
    android_build = re.search(r"\bversionCode\s+(\d+)", android)
    ios_versions = set(re.findall(r"\bMARKETING_VERSION\s*=\s*([^;\s]+)\s*;", ios))
    ios_builds = set(re.findall(r"\bCURRENT_PROJECT_VERSION\s*=\s*(\d+)\s*;", ios))
    mobile_metadata_match = bool(
        android_version
        and android_build
        and len(ios_versions) == 1
        and len(ios_builds) == 1
        and android_version.group(1) == next(iter(ios_versions))
        and android_build.group(1) == next(iter(ios_builds))
    )
    version = android_version.group(1) if mobile_metadata_match and android_version else None
    build = int(android_build.group(1)) if mobile_metadata_match and android_build else None
    valid = mobile_metadata_match and version == APP_VERSION and build == APP_BUILD
    return {
        "ready": valid,
        "version": version,
        "build": build,
        "detail": (
            "backend, iOS, and Android metadata match"
            if valid
            else "backend, iOS, and Android version/build metadata are missing or inconsistent"
        ),
    }


def release_configuration_status(
    repo_root: str | pathlib.Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return booleans and non-secret reasons for each release integration."""
    root = pathlib.Path(repo_root)
    env = os.environ if environment is None else environment
    required_files = (
        root / "Dockerfile",
        root / ".dockerignore",
        root / "render.yaml",
        root / ".env.example",
        root / "lottery_monitor.py",
        root / "requirements.txt",
        root / "chusennote" / "models.py",
        root / "chusennote" / "notifications.py",
        root / "chusennote" / "preflight.py",
        root / "chusennote" / "schema.py",
        root / "chusennote" / "smoke.py",
        root / "chusennote" / "web.py",
        root / ".github" / "workflows" / "android.yml",
        root / ".github" / "workflows" / "docker.yml",
        root / ".github" / "workflows" / "hosted-monitor.yml",
        root / ".github" / "workflows" / "ios.yml",
        root / ".github" / "workflows" / "postgres.yml",
        root / ".github" / "workflows" / "python.yml",
        root / "ios" / "Chusennote.xcodeproj" / "project.pbxproj",
        root / "ios" / "Chusennote.xcodeproj" / "xcshareddata" / "xcschemes" / "Chusennote.xcscheme",
        root / "ios" / "Chusennote" / "Assets.xcassets" / "AppIcon.appiconset" / "Contents.json",
        root / "ios" / "Chusennote" / "Chusennote.entitlements",
        root / "ios" / "Chusennote" / "Info.plist",
        root / "android" / "gradlew",
        root / "android" / "app" / "build.gradle",
        root / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
        root / "android" / "app" / "src" / "main" / "res" / "drawable" / "ic_launcher.xml",
        root / "scripts" / "run-chusennote-once.sh",
        root / "scripts" / "run-hosted-monitor.py",
        root / "scripts" / "check-chusennote.ps1",
        root / "scripts" / "start-chusennote.ps1",
    )
    missing_release_files = [path.relative_to(root).as_posix() for path in required_files if not path.is_file()]
    release_metadata = _release_metadata_status(root)

    database_ready = _database_url_ready(env)
    search_provider = env.get("CHUSENNOTE_SEARCH_PROVIDER", "").strip().lower()
    discovery_ready = search_provider in {"tavily", "brave", "bing", "serpapi"} and _present(
        env, "CHUSENNOTE_SEARCH_API_KEY"
    )
    fcm_project_id = env.get("CHUSENNOTE_FIREBASE_PROJECT_ID", "").strip()
    fcm_project_ready = bool(fcm_project_id)
    explicit_adc_ready = _explicit_credential_file(env)
    fcm_ready = fcm_project_ready and explicit_adc_ready
    (
        ios_firebase_ready,
        ios_firebase_detail,
        ios_firebase_project,
        ios_sender_id,
    ) = _ios_firebase_status(root)
    ios_signing_ready, ios_signing_detail = _ios_signing_status(root)
    (
        android_firebase_ready,
        android_firebase_detail,
        android_firebase_project,
        android_sender_id,
    ) = _android_firebase_status(root)
    if ios_firebase_ready and fcm_project_ready and ios_firebase_project != fcm_project_id:
        ios_firebase_ready = False
        ios_firebase_detail = "iOS Firebase client targets another backend Firebase project"
    if android_firebase_ready and fcm_project_ready and android_firebase_project != fcm_project_id:
        android_firebase_ready = False
        android_firebase_detail = "Android Firebase client targets another backend Firebase project"
    if ios_firebase_ready and android_firebase_ready and ios_sender_id != android_sender_id:
        ios_firebase_ready = False
        android_firebase_ready = False
        ios_firebase_detail = "iOS and Android Firebase clients use different sender projects"
        android_firebase_detail = "iOS and Android Firebase clients use different sender projects"

    signing_names = (
        "CHUSENNOTE_ANDROID_KEYSTORE",
        "CHUSENNOTE_ANDROID_STORE_PASSWORD",
        "CHUSENNOTE_ANDROID_KEY_ALIAS",
        "CHUSENNOTE_ANDROID_KEY_PASSWORD",
    )
    signing_presence = [_present(env, name) for name in signing_names]
    keystore_path = env.get("CHUSENNOTE_ANDROID_KEYSTORE", "").strip()
    signing_ready = all(signing_presence) and pathlib.Path(keystore_path).expanduser().is_file()
    signing_detail = (
        "configured"
        if signing_ready
        else "partially configured"
        if any(signing_presence)
        else "not configured"
    )

    smtp_ready = _present(env, "CHUSENNOTE_SMTP_HOST") and _present(env, "CHUSENNOTE_NOTIFY_EMAIL")
    slack_ready = _webhook_ready(
        env,
        "CHUSENNOTE_SLACK_WEBHOOK_URL",
        {"hooks.slack.com", "hooks.slack-gov.com"},
        "/services/",
    )
    discord_ready = _webhook_ready(
        env,
        "CHUSENNOTE_DISCORD_WEBHOOK_URL",
        {"discord.com"},
        "/api/webhooks/",
    )
    line_ready = _present(env, "CHUSENNOTE_LINE_CHANNEL_ACCESS_TOKEN") and _present(
        env, "CHUSENNOTE_LINE_TARGET_ID"
    )

    components: dict[str, dict[str, object]] = {
        "database": {
            "configured": database_ready,
            "detail": "PostgreSQL database URL configured" if database_ready else "valid PostgreSQL database URL is required",
        },
        "discovery": {
            "configured": discovery_ready,
            "detail": "managed search configured" if discovery_ready else "supported provider and API key are required",
        },
        "fcm-backend": {
            "configured": fcm_ready,
            "detail": (
                "Firebase project and explicit ADC file configured"
                if fcm_ready
                else "Firebase project and readable GOOGLE_APPLICATION_CREDENTIALS file are required; ambient workload identity must be verified after deployment"
            ),
        },
        "ios-firebase": {
            "configured": ios_firebase_ready,
            "detail": ios_firebase_detail,
        },
        "ios-signing": {
            "configured": ios_signing_ready,
            "detail": ios_signing_detail,
        },
        "android-firebase": {
            "configured": android_firebase_ready,
            "detail": android_firebase_detail,
        },
        "android-signing": {"configured": signing_ready, "detail": signing_detail},
        "smtp": {
            "configured": smtp_ready,
            "detail": "SMTP host and local recipient configured" if smtp_ready else "SMTP host and local recipient are not both configured",
        },
        "slack": {"configured": slack_ready, "detail": "valid webhook configured" if slack_ready else "valid Slack webhook is absent"},
        "discord": {
            "configured": discord_ready,
            "detail": "valid webhook configured" if discord_ready else "valid Discord webhook is absent",
        },
        "line": {
            "configured": line_ready,
            "detail": "access token and target configured" if line_ready else "access token and target are not both configured",
        },
    }
    return {
        "release_files_ready": not missing_release_files and release_metadata["ready"],
        "missing_release_files": missing_release_files,
        "release_metadata": release_metadata,
        "components": components,
        "unconfigured": [name for name, status in components.items() if not status["configured"]],
        "production_configuration_required": list(PRODUCTION_PREFLIGHT_COMPONENTS),
        "production_configuration_unconfigured": [
            name for name in PRODUCTION_PREFLIGHT_COMPONENTS if not components[name]["configured"]
        ],
        "production_configuration_ready": bool(
            not missing_release_files
            and release_metadata["ready"]
            and all(components[name]["configured"] for name in PRODUCTION_PREFLIGHT_COMPONENTS)
        ),
        "external_acceptance_required": [
            "deployed-service-smoke",
            "ios-physical-device-push",
            "android-physical-device-push",
        ],
        "external_acceptance_verified": False,
        "release_ready": False,
        "release_ready_detail": "preflight validates configuration only; deployed-service and physical-device acceptance are separate gates",
    }
