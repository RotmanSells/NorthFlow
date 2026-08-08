from pathlib import Path
from northflow.checks import scan_forbidden, scan_files_for_forbidden, is_allowed_scan_path

def test_scan_main_code_blocks():
    assert scan_forbidden("const x: any = 1;") == ["\\bany\\b (строка 1)"]
    assert scan_forbidden("// @ts-ignore") == ["@ts-ignore (строка 1)"]
    assert scan_forbidden("eval(input)") == ["\\beval\\s*\\( (строка 1)"]

def test_scan_allows_tests_and_generated():
    assert scan_forbidden("const x: any = 1;", file_path="src/a.test.ts") == []
    assert scan_forbidden("const x: any = 1;", file_path="src/a.generated.ts") == []
    assert is_allowed_scan_path("scripts/build.ts")
    assert not is_allowed_scan_path("src/app.ts")

def test_scan_allows_fixme_line():
    text = "// FIXME: any здесь временно\nconst x: any = 1;"
    hits = scan_forbidden(text, file_path="src/a.ts")
    assert "\\bany\\b (строка 2)" in hits
    assert not any("строка 1" in h for h in hits)

def test_scan_files(tmp_path: Path):
    (tmp_path / "a.ts").write_text("const x: any = 1;")
    (tmp_path / "a.test.ts").write_text("const x: any = 1;")
    res = scan_files_for_forbidden(tmp_path, ["a.ts", "a.test.ts"])
    assert "a.ts" in res
    assert "a.test.ts" not in res
