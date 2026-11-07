import unittest

import tests.helpers  # noqa: F401  (import path)
from redhanded.classify import classify, is_related_test, match_glob
from redhanded.diffs import change_from_texts, parse_unified_diff

PATCH = """diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 def f():
-    return 1
+    return 2
+
 x = 1
diff --git "a/odd name \\303\\251.py" "b/odd name \\303\\251.py"
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ "b/odd name \\303\\251.py"
@@ -0,0 +1 @@
+print('hi')
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
diff --git a/img.png b/img.png
index 4444444..5555555 100644
Binary files a/img.png and b/img.png differ
diff --git a/gone.py b/gone.py
deleted file mode 100644
index 6666666..0000000
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


class DiffTest(unittest.TestCase):
    def test_parse(self):
        changes = {c.path: c for c in parse_unified_diff(PATCH)}
        self.assertEqual(set(changes), {"src/a.py", "odd name é.py", "new.py", "img.png", "gone.py"})
        a = changes["src/a.py"]
        self.assertEqual([l.text for l in a.added()], ["    return 2", ""])
        self.assertEqual([l.text for l in a.removed()], ["    return 1"])
        self.assertEqual(changes["odd name é.py"].status, "A")
        self.assertEqual(changes["new.py"].status, "R")
        self.assertEqual(changes["new.py"].old_path, "old.py")
        self.assertTrue(changes["img.png"].binary)
        self.assertEqual(changes["gone.py"].status, "D")

    def test_line_numbers(self):
        ch = change_from_texts("a.py", "a\nb\nc\n", "a\nB\nc\nd\n")
        self.assertEqual([(l.text, l.new_no) for l in ch.added()], [("B", 2), ("d", 4)])
        self.assertEqual([(l.text, l.old_no) for l in ch.removed()], [("b", 2)])

    def test_crlf_and_missing_newline(self):
        ch = change_from_texts("a.py", "a\r\nb\r\n", "a\r\nb\r\nc")
        self.assertEqual([l.text for l in ch.added()], ["c"])


class GlobTest(unittest.TestCase):
    def test_match_glob(self):
        cases = [
            ("vendor/x/y.py", "vendor/**", True),
            ("src/vendor/x.py", "vendor/**", False),
            ("a/b/c.test.ts", "**/*.test.ts", True),
            ("c.test.ts", "**/*.test.ts", True),
            ("deep/x.snap", "*.snap", True),  # no slash: matches at any depth, like .gitignore
            ("legacy/a.py", "legacy/*.py", True),
            ("legacy/sub/a.py", "legacy/*.py", False),
            ("src/a.py", "src/?.py", True),
        ]
        for path, pattern, want in cases:
            self.assertEqual(match_glob(path, pattern), want, (path, pattern))


class ClassifyTest(unittest.TestCase):
    def test_tests_by_language(self):
        tests = ["tests/test_api.py", "api_test.py", "src/cart.test.ts", "src/cart.spec.tsx", "e2e/login.cy.js",
                 "pkg/cart_test.go", "spec/cart_spec.rb", "src/test/java/CartTest.java", "CartTests.cs",
                 "tests/CartTest.php", "Tests/CartTests.swift", "test/cart_test.exs", "tests/cart.rs",
                 "src/Button.test.vue", "__tests__/cart.js"]
        for p in tests:
            self.assertTrue(classify(p).is_test, p)
        for p in ("src/cart.ts", "src/testing.py", "src/contest.py", "latest.go", "attestation.rs", "README.md"):
            self.assertFalse(classify(p).is_test, p)

    def test_kinds(self):
        self.assertTrue(classify("src/__snapshots__/a.test.ts.snap").is_snapshot)
        self.assertTrue(classify("docs/guide.md").is_doc)
        self.assertTrue(classify("dist/bundle.min.js").is_generated)
        self.assertIn("ci", classify(".github/workflows/ci.yml").config)
        self.assertIn("test-runner", classify("jest.config.ts").config)
        self.assertTrue(classify("tests/conftest.py").is_test_support)

    def test_extra_globs_and_ignore(self):
        self.assertTrue(classify("checks/cart_check.py", extra_test_globs=["checks/**"]).is_test)
        self.assertTrue(classify("vendor/lib.py", ignore_globs=["vendor/**"]).is_generated)

    def test_related(self):
        self.assertTrue(is_related_test("tests/test_cart.py", "shop/cart.py"))
        self.assertTrue(is_related_test("src/cart.test.ts", "src/cart.ts"))
        self.assertTrue(is_related_test("pkg/cart_test.go", "pkg/cart.go"))
        self.assertFalse(is_related_test("tests/test_user.py", "shop/cart.py"))


if __name__ == "__main__":
    unittest.main()
