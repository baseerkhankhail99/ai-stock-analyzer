import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def ensure(condition, message):
    if not condition:
        raise AssertionError(message)


def read_source(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def parse_source(relative_path):
    return ast.parse(read_source(relative_path))


def test_app_defaults_host_to_localhost():
    app_tree = parse_source("app.py")

    for node in ast.walk(app_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "HOST"
            and isinstance(node.args[1], ast.Constant)
        ):
            ensure(
                node.args[1].value == "127.0.0.1",
                "Expected HOST default to use localhost.",
            )
            return

    raise AssertionError("Expected HOST default to be set with os.getenv().")


def test_dashboard_calls_services_in_process_without_http():
    dashboard_tree = parse_source("dashboard.py")

    for node in ast.walk(dashboard_tree):
        if isinstance(node, ast.Import):
            ensure(
                all(alias.name != "requests" for alias in node.names),
                "Dashboard must not make HTTP requests to its own API.",
            )
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            ensure(
                not (node.value.id == "requests" and node.attr == "get"),
                "Dashboard must call the service functions in-process.",
            )


def test_slack_notifications_are_skipped_without_secret():
    workflow_lines = read_source(".github/workflows/ci-cd.yml").splitlines()
    slack_steps = 0

    for index, line in enumerate(workflow_lines):
        if line.strip() != "- name: Notify Slack (optional)":
            continue
        slack_steps += 1
        step_lines = []
        for next_line in workflow_lines[index + 1 :]:
            if next_line.strip().startswith("- name:") or (
                next_line.strip() and not next_line.startswith("      ")
            ):
                break
            step_lines.append(next_line.strip())

        conditions = [x for x in step_lines if x.startswith("if:")]
        ensure(len(conditions) == 1, "Expected one Slack step condition.")
        normalized = conditions[0].replace('"', "'").replace(" ", "")
        ensure("always()" in normalized, "Expected Slack step to always evaluate.")
        ensure(
            "env.SLACK_WEBHOOK_URL!=''" in normalized,
            "Expected Slack step to require a non-empty webhook env var.",
        )
        ensure(
            "secrets." not in normalized,
            "secrets context is not valid in step-level if conditions.",
        )
        ensure(
            "continue-on-error: true" in step_lines,
            "Expected Slack step to use continue-on-error.",
        )

    ensure(slack_steps >= 1, "Expected to find Slack notification steps.")
