"""Protect the user's decoupling requirement as modules evolve."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/bank_project"
BUSINESS = {
    path.name
    for path in ROOT.iterdir()
    if path.is_dir()
    and path.name not in {"api", "adapters", "contracts"}
    and any(path.rglob("*.py"))
}


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"Use absolute imports so boundaries are inspectable: {path}"
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def test_business_modules_only_depend_on_contracts_and_ports():
    for module in BUSINESS:
        for path in (ROOT / module).rglob("*.py"):
            for imported in imports(path):
                if imported.startswith("bank_project."):
                    target = imported.split(".")[1]
                    assert target in {"contracts", "ports", module}, (
                        f"{path}: {imported} crosses a module boundary"
                    )
                else:
                    assert imported.split(".")[0] in sys.stdlib_module_names, (
                        f"{path}: {imported} couples business logic to infrastructure"
                    )


def test_contracts_and_ports_do_not_depend_on_implementations():
    for path in [ROOT / "ports.py", *(ROOT / "contracts").rglob("*.py")]:
        for imported in imports(path):
            if imported.startswith("bank_project."):
                assert imported.split(".")[1] == "contracts"
            else:
                assert imported.split(".")[0] in sys.stdlib_module_names | {"pydantic"}


def test_api_does_not_import_business_implementations_or_adapters():
    for path in (ROOT / "api").rglob("*.py"):
        for imported in imports(path):
            if imported.startswith("bank_project."):
                assert imported.split(".")[1] in {
                    "api",
                    "application",
                    "contracts",
                    "ports",
                    "__version__",
                }
            else:
                assert imported.split(".")[0] in sys.stdlib_module_names | {"fastapi", "pydantic"}


def test_adapters_do_not_import_business_implementations_or_api():
    for path in (ROOT / "adapters").rglob("*.py"):
        for imported in imports(path):
            if imported.startswith("bank_project."):
                assert imported.split(".")[1] in {"adapters", "contracts", "ports"}
