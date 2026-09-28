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


def test_dashboard_wraps_requests_with_timeout_helper():
    dashboard_tree = parse_source("dashboard.py")
    helper = next(
        (
            node
            for node in dashboard_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "fetch_api_response"
        ),
        None,
    )

    ensure(helper is not None, "Expected fetch_api_response helper to exist.")

    request_calls = [
        node
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
        and node.func.attr == "get"
    ]

    ensure(
        len(request_calls) == 1,
        "Expected helper to contain exactly one requests.get call.",
    )

    timeout_keywords = {
        keyword.arg: keyword.value
        for keyword in request_calls[0].keywords
        if keyword.arg
    }
    timeout_value = timeout_keywords.get("timeout")

    ensure(
        isinstance(timeout_value, ast.Name),
        "Expected timeout to reference a named constant.",
    )
    ensure(
        timeout_value.id == "REQUEST_TIMEOUT_SECONDS",
        "Expected timeout to use REQUEST_TIMEOUT_SECONDS.",
    )


def test_dashboard_uses_timeout_helper_for_all_http_gets():
    dashboard_tree = parse_source("dashboard.py")

    request_get_call_count = 0
    helper_call_functions = set()

    for function in [
        node for node in dashboard_tree.body if isinstance(node, ast.FunctionDef)
    ]:
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr == "get"
            ):
                request_get_call_count += 1

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "fetch_api_response"
                and function.name != "fetch_api_response"
            ):
                helper_call_functions.add(function.name)

    ensure(
        request_get_call_count == 1,
        "Expected dashboard requests.get usage to be centralized in the helper.",
    )
    ensure(
        helper_call_functions
        == {
            "update_stock_analysis",
            "update_price_chart",
            "update_technical_signals",
            "update_comparison",
        },
        "Expected dashboard callbacks to use the timeout helper.",
    )


def test_slack_notifications_are_skipped_without_secret():
    workflow_lines = read_source(".github/workflows/ci-cd.yml").splitlines()
    slack_conditions = []

    for index, line in enumerate(workflow_lines):
        if line.strip() == "- name: Notify Slack (optional)":
            for next_line in workflow_lines[index + 1 :]:
                stripped = next_line.strip()
                if stripped.startswith("- name:"):
                    break
                if stripped.startswith("if:"):
                    condition = stripped.removeprefix("if:").strip()
                    if condition.startswith("${{") and condition.endswith("}}"):
                        condition = condition[3:-2].strip()
                    slack_conditions.append(condition)
                    break

    ensure(len(slack_conditions) == 2, "Expected two Slack notification conditions.")
    for condition in slack_conditions:
        normalized = condition.replace('"', "'").replace(" ", "")
        ensure(
            "always()" in normalized, "Expected Slack step to always evaluate post-job."
        )
        ensure(
            "secrets.SLACK_WEBHOOK!=''" in normalized,
            "Expected Slack step to require a non-empty SLACK_WEBHOOK secret.",
        )
