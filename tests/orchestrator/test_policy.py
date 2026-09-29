import pytest

from orchestrator.lifecycle import POLICY_PATH
from orchestrator.models import NodeSpec, PolicyViolation, Risk
from orchestrator.policy import FileChange, Policy


@pytest.fixture(scope="module")
def policy() -> Policy:
    return Policy.load(POLICY_PATH)


def spec(scope=("shortener/**", "tests/**"), risk=Risk.LOW, approval=None):
    return NodeSpec("impl:X", "implement", "implement", write_scope=list(scope), risk=risk, approval=approval)


def test_protected_paths_are_never_writable(policy):
    with pytest.raises(PolicyViolation, match="protected_path"):
        policy.enforce_change_set(spec(scope=["**"]), [FileChange("governance/policy.yaml", "modified", ["x"])])


def test_writes_outside_node_scope_are_violations(policy):
    with pytest.raises(PolicyViolation, match="write_scope"):
        policy.enforce_change_set(spec(scope=["shortener/app.py"]), [FileChange("shortener/db.py", "modified", ["x"])])


@pytest.mark.parametrize("line", [
    'ADMIN_API_TOKEN = "sk_live_51HqLyjWDarjtT1zdp7dcXd"',
    'db_password = "correct-horse-battery"',
    "aws = 'AKIAABCDEFGHIJKLMNOP'",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_secrets_are_blocked(policy, line):
    with pytest.raises(PolicyViolation, match="security.secret"):
        policy.enforce_change_set(spec(), [FileChange("shortener/x.py", "added", [line])])


@pytest.mark.parametrize("line", [
    "result = eval(user_input)",
    "subprocess.run(cmd, shell=True)",
    "requests.get(url, verify=False)",
    'conn.execute(f"SELECT * FROM t WHERE id = {x}")',
])
def test_dangerous_code_is_blocked(policy, line):
    with pytest.raises(PolicyViolation, match="security.code"):
        policy.enforce_change_set(spec(), [FileChange("shortener/x.py", "added", [line])])


def test_safe_code_passes(policy):
    lines = ['salt = secrets.token_hex(16)', 'conn.execute("SELECT * FROM t WHERE id = ?", (x,))']
    assert policy.enforce_change_set(spec(), [FileChange("shortener/x.py", "added", lines)]) == []


def test_pii_columns_in_migrations_are_blocked(policy):
    change = FileChange("shortener/db.py", "modified", ["    client_ip TEXT NOT NULL,"])
    with pytest.raises(PolicyViolation, match="pii_column"):
        policy.enforce_change_set(spec(), [change])


def test_dependency_allowlist(policy):
    s = spec(scope=["requirements.txt"])
    policy.enforce_change_set(s, [FileChange("requirements.txt", "added", ["fastapi>=0.110", "# comment"])])
    with pytest.raises(PolicyViolation, match="dependency_allowlist"):
        policy.enforce_change_set(s, [FileChange("requirements.txt", "added", ["left-pad==1.0"])])


def test_change_size_limits(policy):
    changes = [FileChange(f"shortener/m{i}.py", "added", ["x = 1"]) for i in range(policy.max_files + 1)]
    with pytest.raises(PolicyViolation, match="max_files"):
        policy.enforce_change_set(spec(), changes)


def test_classification_of_high_impact_actions(policy):
    changes = [
        FileChange("shortener/db.py", "modified", ["CREATE TABLE t (id INTEGER)"]),
        FileChange("requirements.txt", "modified", ["httpx"]),
        FileChange("shortener/app.py", "modified", ["@app.get('/x')"]),
        FileChange("shortener/old.py", "deleted", [], 10),
    ]
    assert policy.classify(changes) == {"schema_migration", "new_dependency", "public_api_change", "deletion"}


def test_approval_routing(policy):
    assert policy.approval_reasons(spec(), set()) == []
    assert any("risk" in r for r in policy.approval_reasons(spec(risk=Risk.MEDIUM), set()))
    assert any("schema_migration" in r for r in policy.approval_reasons(spec(), {"schema_migration"}))
    assert any("mandatory" in r for r in policy.approval_reasons(spec(approval="release"), set()))
    assert not policy.can_auto_approve("release")
