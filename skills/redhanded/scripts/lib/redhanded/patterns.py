"""Regex catalog, grouped by language family.

Families: js (JS/TS), py, go, rust, java (Java/Kotlin/Groovy), scala, csharp, ruby, php, swift, dart,
elixir, cpp (C/C++/ObjC), shell (bats).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Pattern, Sequence, Tuple


def _any(*parts: str, flags: int = 0) -> Pattern[str]:
    return re.compile("|".join("(?:%s)" % p for p in parts), flags)


B = r"(?<![\w.$])"  # start of a bare identifier (not obj.method, not $var)

# --------------------------------------------------------------------------- test skipping

SKIP: Dict[str, Pattern[str]] = {
    "js": _any(
        r"\b(?:it|test|describe|suite|context|specify|bench)\.skip\b",
        B + r"x(?:it|test|describe|context|specify)\s*\(",
        r"\btest\.fixme\s*\(",
        r"\bthis\.skip\s*\(\s*\)",
        B + r"pending\s*\(\s*\)",
    ),
    "py": _any(
        r"@pytest\.mark\.(?:skip|xfail)\b(?!if)",
        r"\bpytest\.(?:skip|xfail)\s*\(",
        r"^\s*@(?:unittest\.)?skip\s*\(",
        r"@unittest\.expectedFailure\b|^\s*@expectedFailure\b",
        r"\bself\.skipTest\s*\(",
        r"\braise\s+(?:unittest\.)?SkipTest\b",
        r"\bpytestmark\s*=\s*pytest\.mark\.(?:skip|xfail)\b",
    ),
    "go": _any(r"\b[tb]\.Skip(?:f|Now)?\s*\("),
    "rust": _any(r"#\[\s*ignore\b"),
    "java": _any(r"@(?:Disabled|Ignore)\b(?!If|On)", r"\bassumeTrue\s*\(\s*false\s*\)", r"@Tag\(\s*\"(?:skip|disabled)\""),
    "scala": _any(B + r"ignore\s*\(\s*\"", r"\bpendingUntilFixed\b", B + r"ignore\s+\"[^\"]*\"\s+in\b"),
    "csharp": _any(
        r"\[\s*(?:Fact|Theory)\s*\(\s*Skip\s*=",
        r"\[\s*(?:Ignore|Skip|Explicit)\b",
        r",\s*Ignore\s*\]",
        r"\bAssert\.(?:Ignore|Inconclusive)\s*\(",
    ),
    "ruby": _any(
        r"^\s*(?:xit|xspecify|xdescribe|xcontext|xscenario|xexample)\b",
        r"^\s*skip(?:\s|\(|$)",
        r"^\s*pending(?:\s|\(|$)",
        r"\bskip:\s*(?:true|['\"])",
        r":skip\s*=>\s*true",
    ),
    "php": _any(r"\bmarkTest(?:Skipped|Incomplete)\s*\(", r"->skip\s*\(", r"@group\s+(?:skip|disabled)\b"),
    "swift": _any(r"\bthrow\s+XCTSkip\b", r"\.disabled\s*\("),
    "dart": _any(r"\bskip\s*:\s*(?:true|['\"])"),
    "elixir": _any(r"@(?:module)?tag\s+(?::skip\b|skip:)"),
    "cpp": _any(r"\bDISABLED_\w+", r"\bGTEST_SKIP\s*\(", B + r"SKIP\s*\(", r"\"\[!?\.\]"),
    "shell": _any(r"^\s*skip\b"),
}

CONDITIONAL_SKIP: Dict[str, Pattern[str]] = {
    "js": _any(r"\b(?:it|test|describe)\.(?:skipIf|runIf)\s*\("),
    "py": _any(r"@pytest\.mark\.skipif\b", r"@(?:unittest\.)?skip(?:If|Unless)\b"),
    "java": _any(r"@(?:Disabled|Enabled)(?:If|On)\w*", r"\bAssum(?:e|ptions)\.assume\w*\s*\("),
    "csharp": _any(r"\bSkip\.(?:If|IfNot|Unless)\s*\("),
    "swift": _any(r"\bXCTSkip(?:If|Unless)\s*\("),
}

TODO_TEST: Dict[str, Pattern[str]] = {
    "js": _any(r"\b(?:it|test|describe)\.todo\s*\("),
    "php": _any(r"->todo\s*\("),
}

FOCUS: Dict[str, Pattern[str]] = {
    "js": _any(r"\b(?:it|test|describe|context|suite|specify)\.only\b", B + r"f(?:it|describe|context)\s*\("),
    "ruby": _any(r"^\s*f(?:it|describe|context|specify)\b", r"\bfocus:\s*true\b", r"(?:,|\s):focus\b"),
    "dart": _any(r"\bsolo\s*:\s*true\b"),
    "php": _any(r"->only\s*\("),
}

RETRIES: Dict[str, Pattern[str]] = {
    "js": _any(r"\bjest\.retryTimes\s*\(", r"\bthis\.retries\s*\(", r"\bretry\s*:\s*[1-9]", r"\bretries\s*:\s*[1-9]",
               r"\btest\.describe\.configure\s*\(\s*\{[^}]*retries"),
    "py": _any(r"@pytest\.mark\.flaky\b", r"^\s*@flaky\b", r"--reruns\b", r"@retry\b"),
    "java": _any(r"@RetryingTest\b", r"@Retry\b", r"@RepeatedIfExceptionsTest\b"),
    "csharp": _any(r"\[\s*Retry\s*\(", r"\[\s*RetryFact\b"),
    "ruby": _any(r"\bretry:\s*[1-9]", r"\brspec-retry\b"),
    "go": _any(r"\bretry\.\w+\("),
}

# --------------------------------------------------------------------------- assertions

ASSERT: Dict[str, Pattern[str]] = {
    "js": _any(
        B + r"(?:expect|assert)\w*\s*\(",
        r"\bassert\.\w+\s*\(",
        r"\.should\b",
        r"\bt\.(?:is|not|deepEqual|notDeepEqual|true|false|truthy|falsy|throws|throwsAsync|notThrows|notThrowsAsync"
        r"|like|regex|notRegex|snapshot|pass|fail|assert|equal|same|ok|match)\s*\(",
    ),
    "py": _any(
        r"^\s*assert\b",
        r"\bself\.assert\w*\s*\(",
        B + r"assert\w*\s*\(",
        r"\.assert_\w+\s*\(",
        r"\bpytest\.(?:raises|warns|fail)\s*\(",
    ),
    "go": _any(r"\b(?:assert|require)\.\w+\s*\(", r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(",
               r"\bcmp\.Diff\s*\(", B + r"Expect\s*\(", r"\bqt\.Assert\s*\("),
    "rust": _any(r"\b(?:debug_)?assert(?:_eq|_ne)?!\s*\(", r"\bassert_matches!\s*\(", r"\bprop_assert\w*!\s*\(",
                 r"\bassert_\w+!\s*\("),
    "java": _any(B + r"assert\w*\s*\(", r"\bAssert(?:ions)?\.\w+\s*\(", r"\bverify\s*\(", r"\bshould\w*\b",
                 B + r"expect\s*\(", r"^\s*assert\s"),
    "scala": _any(B + r"assert\w*\s*\(", r"\bshould(?:Be|Equal)?\b", r"\bmust(?:Be|Equal)\b", B + r"expect\s*\("),
    "csharp": _any(r"\b(?:Assert|CollectionAssert|StringAssert|ClassicAssert)\.\w+\s*\(", r"\.Should\(\)",
                   r"\.Verify\s*\("),
    "ruby": _any(B + r"expect\s*[\(\{]", B + r"assert\w*\b", B + r"refute\w*\b", r"\.(?:should|must|wont)\b",
                 r"\bis_expected\b"),
    "php": _any(r"\$this->assert\w+\s*\(", r"(?:self|static|parent)::assert\w+\s*\(", r"\bAssert::\w+\s*\(",
                r"(?<![\w>$])expect\s*\("),
    "swift": _any(r"\bXCTAssert\w*\s*\(", r"\bXCTFail\s*\(", r"#expect\s*\(", r"#require\s*\("),
    "dart": _any(B + r"expect(?:Later|Async)?\s*\("),
    "elixir": _any(B + r"(?:assert|refute)(?:_\w+)?\b"),
    "cpp": _any(r"\b(?:EXPECT|ASSERT)_\w+\s*\(", r"\b(?:REQUIRE|CHECK)(?:_\w+)?\s*\(", r"\bBOOST_(?:CHECK|REQUIRE)\w*\s*\("),
    "shell": _any(B + r"assert\w*\b", r"\brefute\w*\b", r"\[\s+\"\$(?:status|output)\""),
}

# Strength of a single assertion line: higher is stricter. First match wins (most specific first).
STRENGTH: Dict[str, List[Tuple[Pattern[str], int]]] = {
    "js": [
        (re.compile(r"\.not\.toBe(?:Null|Undefined)\(\s*\)|\.toBe(?:Defined|Truthy)\(\s*\)|expect\.(?:anything|any)\(|"
                    r"\.not\.toThrow\(|\.to\.(?:exist|be\.ok)\b|\.toBeTypeOf\("), 1),
        (re.compile(r"\.toStrictEqual\(|\.toHaveBeenNthCalledWith\(|\.toHaveBeenLastCalledWith\(|\.toHaveBeenCalledWith\(|"
                    r"\.toThrow(?:Error)?\(\s*[^\s)]|assert\.(?:deepStrictEqual|strictEqual)\("), 5),
        (re.compile(r"\.toEqual\(|\.toBe\(\s*(?!true\b|false\b|null\b|undefined\b)[^\s)]|\.toMatchObject\(|"
                    r"\.toHaveLength\(|\.toHaveBeenCalledTimes\(|\.toMatch(?:Inline)?Snapshot\(|"
                    r"assert\.(?:deepEqual|equal)\(|\.to\.(?:deep\.)?equal\(|\.to\.eql\(|\bt\.(?:is|deepEqual|like)\("), 4),
        (re.compile(r"\.toContain(?:Equal)?\(|\.toMatch\(|\.toBeCloseTo\(|\.toHaveProperty\(|\.toBe\(\s*(?:true|false|null|undefined)\s*\)|"
                    r"\.toBe(?:Null|Undefined|NaN)\(\)|\.to\.include\(|\.toHave(?:TextContent|Value|Attribute)\("), 3),
        (re.compile(r"\.toBe(?:GreaterThan|LessThan)(?:OrEqual)?\(|\.toHaveBeenCalled\(\s*\)|\.toBeInstanceOf\(|"
                    r"\.toThrow\(\s*\)|\.toBeFalsy\(\)|\.toBeInTheDocument\(|\.toBeVisible\(|\bt\.(?:true|false|truthy|falsy)\("), 2),
    ],
    "py": [
        (re.compile(r"assertIsNotNone\(|\.assert_called\(\s*\)|"
                    r"(?:assertRaises|pytest\.raises)\(\s*(?:Exception|BaseException)\s*[,)]|"
                    r"^\s*assert\s+(?:not\s+)?[\w.\[\]'\"()]+\s*(?:,.*)?(?:#.*)?$"), 1),
        (re.compile(r"assertRaisesRegexp?\(|pytest\.raises\([^)]*match\s*="), 5),
        (re.compile(r"assert(?:Equals?|DictEqual|ListEqual|TupleEqual|SetEqual|CountEqual|MultiLineEqual|SequenceEqual)\(|"
                    r"\.assert_called(?:_once)?_with\(|\.assert_has_calls\(|assert_(?:array_)?equal\(|assert_frame_equal\(|"
                    r"^\s*assert\s+.+?\s(?:==|!=|is\s+not|is)\s"), 4),
        (re.compile(r"assert(?:In|NotIn|Regex|AlmostEqual|IsNone|Raises|NotEqual)\(|pytest\.raises\(|\.assert_any_call\(|"
                    r"^\s*assert\s+.+\s(?:not\s+)?in\s|approx\("), 3),
        (re.compile(r"assert(?:True|False|Greater\w*|Less\w*|IsInstance)\(|\.assert_called_once\(\s*\)|"
                    r"^\s*assert\s+.+\s(?:<=|>=|<|>)\s"), 2),
    ],
    "go": [
        (re.compile(r"(?:assert|require)\.(?:NotNil|NotEmpty|NotZero|True|False)\("), 1),
        (re.compile(r"(?:assert|require)\.(?:Equal|EqualValues|Exactly|ElementsMatch|JSONEq|YAMLEq|ErrorIs|ErrorAs|"
                    r"ErrorContains|EqualError|InDelta|InEpsilon|Regexp)\(|cmp\.Diff\(|reflect\.DeepEqual\(|"
                    r"\bif\s+got\s*!=\s*want\b"), 4),
        (re.compile(r"(?:assert|require)\.(?:Contains|Len|Subset|NoError|Panics\w*|Greater\w*|Less\w*|Zero|Nil|Empty)\(|"
                    r"\bt\.(?:Error|Fatal)f?\("), 3),
        (re.compile(r"(?:assert|require)\.Error\("), 2),
    ],
    "rust": [
        (re.compile(r"\bassert!\(\s*[\w.]+\s*\)"), 1),
        (re.compile(r"\bassert_(?:eq|ne)!\(|\bassert_matches!\(|\bassert!\(\s*matches!\("), 4),
        (re.compile(r"\bassert!\(.*(?:==|!=|<=|>=|<|>)"), 3),
        (re.compile(r"\bassert!\(.*\.(?:is_ok|is_err|is_some|is_none|contains|starts_with|ends_with)\("), 2),
    ],
    "java": [
        (re.compile(r"assertNotNull\(|isNotNull\(\)|isNotEmpty\(\)|shouldNotBe\s+null|\.shouldNotBeNull\(\)|"
                    r"assertThrows\(\s*(?:Exception|Throwable|RuntimeException)(?:::class)?(?:\.class)?\s*[,)]"), 1),
        (re.compile(r"assertEquals\(|assertArrayEquals\(|assertIterableEquals\(|assertLinesMatch\(|assertSame\(|"
                    r"isEqualTo\(|containsExactly\w*\(|hasSize\(|\bshouldBe\b|\bshouldEqual\b"), 4),
        (re.compile(r"assertNotEquals\(|\.contains\(|hasMessage\w*\(|assertThrows\(|isInstanceOf\(|\bverify\("), 3),
        (re.compile(r"assertTrue\(|assertFalse\(|isTrue\(\)|isFalse\(\)|assertNull\("), 2),
    ],
    "csharp": [
        (re.compile(r"Assert\.(?:NotNull|IsNotNull|NotEmpty)\(|\.Should\(\)\.(?:NotBeNull|NotBeEmpty)\(|"
                    r"Assert\.Throws(?:Async)?<Exception>"), 1),
        (re.compile(r"Assert\.(?:Equal|AreEqual|Same|AreSame|Throws(?:Async)?<\w+>|Collection|Equivalent|Contains)\(|"
                    r"\.Should\(\)\.(?:Be|Equal|BeEquivalentTo|ContainSingle|HaveCount)\(|\.Should\(\)\.Throw\w*<"), 4),
        (re.compile(r"Assert\.(?:True|IsTrue|False|IsFalse|Null|IsNull)\(|\.Should\(\)\.(?:BeTrue|BeFalse|BeNull)\("), 2),
    ],
    "ruby": [
        (re.compile(r"\.to\s+(?:be_truthy|be_present|exist)\b|\.(?:not_to|to_not)\s+(?:be_nil|be_empty|be_blank)\b|"
                    r"\.to\s+raise_error\s*$|\.to\s+raise_error\(\s*\)|refute_nil\b|\bassert\s+\w+\s*$"), 1),
        (re.compile(r"\.to\s*\(?\s*(?:eq|eql|equal|match_array|contain_exactly|match)\b|\.to\s+raise_error\(\s*\w+|"
                    r"\bassert_equal\b|\bassert_raises\(\s*\w+"), 4),
        (re.compile(r"\.to\s+(?:include|have_attributes|be_within|start_with|end_with|change)\b|assert_includes\b|assert_match\b"), 3),
        (re.compile(r"\.to\s+be\s*[<>]=?|\.to\s+be_(?:a|an|kind_of|instance_of)\b|\bassert\b"), 2),
    ],
    "php": [
        (re.compile(r"assertNotNull\(|assertNotEmpty\(|assertIsObject\(|toBeTruthy\("), 1),
        (re.compile(r"assert(?:Same|Equals|EqualsCanonicalizing|JsonStringEqualsJsonString|Count)\(|toBe\(|toEqual\("), 4),
        (re.compile(r"assert(?:True|False|Null)\("), 2),
    ],
    "swift": [
        (re.compile(r"XCTAssertNotNil\(|XCTAssert\(\s*\w+\s*\)"), 1),
        (re.compile(r"XCTAssertEqual\(|XCTAssertIdentical\(|XCTAssertThrowsError\(|#expect\([^)]*=="), 4),
        (re.compile(r"XCTAssert(?:True|False|Nil)\("), 2),
    ],
    "dart": [
        (re.compile(r"expect\([^,]+,\s*(?:isNotNull|isTrue|isNotEmpty|anything)\b"), 1),
        (re.compile(r"expect\([^,]+,\s*(?:equals|orderedEquals|unorderedEquals|throwsA)\("), 4),
    ],
    "elixir": [
        (re.compile(r"\b(?:assert|refute)\s+\w+\s*$"), 1),
        (re.compile(r"\bassert\s+.+==|\bassert_raise\s+\w+"), 4),
    ],
    "cpp": [
        (re.compile(r"(?:EXPECT|ASSERT)_(?:ANY_THROW|NO_THROW)\(|\bREQUIRE\(\s*\w+\s*\)"), 1),
        (re.compile(r"(?:EXPECT|ASSERT)_(?:EQ|NE|STREQ|DOUBLE_EQ|FLOAT_EQ|NEAR|THROW)\(|\bREQUIRE\(.*=="), 4),
        (re.compile(r"(?:EXPECT|ASSERT)_(?:TRUE|FALSE)\("), 2),
    ],
}

# Assertions that cannot fail.
TAUTOLOGY: List[Pattern[str]] = [
    re.compile(r"expect\(\s*(true|1|'[^']*'|\"[^\"]*\")\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"),
    re.compile(r"expect\(\s*true\s*\)\s*\.toBe(?:Truthy)?\(\s*(?:true)?\s*\)"),
    re.compile(r"expect\(\s*(?:false|null|undefined)\s*\)\s*\.(?:toBeFalsy|toBeNull|toBeUndefined|not\.toBeTruthy)\(\s*\)"),
    re.compile(r"expect\(\s*([A-Za-z_$][\w$.]*)\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"),
    re.compile(r"^\s*assert\s+(?:True|1|not\s+False|not\s+None)\s*(?:,.*)?(?:#.*)?$"),
    re.compile(r"^\s*assert\s+([A-Za-z_][\w.]*)\s*==\s*\1\s*(?:,.*)?(?:#.*)?$"),
    re.compile(r"\bself\.assertTrue\(\s*True\s*\)|\bself\.assertFalse\(\s*False\s*\)"),
    re.compile(r"\bself\.assertEqual\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"\bassert!\(\s*true\s*\)|\bassert_eq!\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"\bassert(?:True|That)\(\s*true\s*\)|\bAssert\.(?:True|IsTrue)\(\s*true\s*\)|\bassertFalse\(\s*false\s*\)"),
    re.compile(r"\bassertEquals\(\s*([^,()]+?)\s*,\s*\1\s*\)|\bAssert\.(?:Equal|AreEqual)\(\s*([^,()]+?)\s*,\s*\2\s*\)"),
    re.compile(r"\bassert\.(?:True\(\s*t\s*,\s*true|Equal\(\s*t\s*,\s*([^,()]+?)\s*,\s*\1)\s*\)"),
    re.compile(r"\bXCTAssert(?:True)?\(\s*true\s*\)|#expect\(\s*true\s*\)"),
    re.compile(r"expect\(\s*true\s*\)\.to\s+(?:be\s+true|be_truthy|eq\(\s*true\s*\))|^\s*assert\s+true\s*$"),
    re.compile(r"\$this->assertTrue\(\s*true\s*\)|\$this->assertFalse\(\s*false\s*\)"),
    re.compile(r"\b(?:EXPECT|ASSERT)_TRUE\(\s*true\s*\)|\b(?:EXPECT|ASSERT)_EQ\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"expect\(\s*true\s*,\s*(?:isTrue|true)\s*\)"),
    re.compile(r"\bassert\.(?:ok|isTrue)\(\s*true\s*\)|\bt\.(?:pass)\(\s*\)"),
]

# Commented-out assertion in a test file.
COMMENTED_ASSERT = _any(
    r"^\s*(?://+|/\*+|\*|--)\s*(?:await\s+)?(?:"
    r"expect\s*\(|assert\w*!?\s*\(|self\.assert\w*\(|XCTAssert\w*\(|t\.(?:Error|Fatal)\w*\(|#expect\(|"
    r"(?:EXPECT|ASSERT)_\w+\(|Assert\.\w+\(|\$this->assert\w+\(|require\.\w+\(|\w+\.should\.|assert\.\w+\()",
    r"^\s*#+\s*(?:"
    r"assert\s+[\w.\[\]()'\"]+\s*(?:==|!=|\bis\b|\bin\b|<|>|,|$)|self\.assert\w*\(|assert\w*\(|expect\s*[\(\{]|"
    r"assert_\w+[\s(]|refute\w*\s)",
)

# --------------------------------------------------------------------------- test declarations

# (pattern, name group indexes). Names are used to tell "moved" tests from "deleted" ones.
TEST_DECL: Dict[str, List[Tuple[Pattern[str], Sequence[int]]]] = {
    "js": [(re.compile(B + r"[xf]?(?:it|test|specify)(?:\.(?!each\b)\w+)*\s*\(\s*(['\"`])(.{0,300}?)\1"), (2,))],
    "py": [(re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test\w*)[ \t]*\(", re.M), (1,))],
    "go": [(re.compile(r"^func[ \t]+(?:\([^)]*\)[ \t]*)?(Test\w*)[ \t]*\(", re.M), (1,))],
    "rust": [(re.compile(r"#\[\s*(?:\w+::)*(?:test|rstest|test_case)\b[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?"
                         r"(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)"), (1,))],
    "java": [(re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b(?:\([^)]*\))?\s*"
                         r"(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|internal|open|override|suspend|static|final)\s+)*"
                         r"(?:void\s+|fun\s+|def\s+)?(?:`([^`]+)`|(\w+))\s*\("), (1, 2))],
    "scala": [(re.compile(B + r"test\s*\(\s*\"([^\"]{0,300})\""), (1,))],
    "csharp": [(re.compile(r"\[\s*(?:Fact|Theory|Test|TestMethod|DataTestMethod|TestCase)\b[^\]]*\]\s*(?:\[[^\]]*\]\s*)*"
                           r"(?:public\s+|internal\s+|private\s+)?(?:async\s+)?(?:static\s+)?(?:[\w<>\[\],.]+)\s+(\w+)\s*\("), (1,))],
    "ruby": [(re.compile(B + r"[xf]?(?:it|specify|scenario|example|test)\s*\(?\s*(['\"])(.{0,300}?)\1"), (2,)),
             (re.compile(r"^[ \t]*def[ \t]+(test_\w+)", re.M), (1,))],
    "php": [(re.compile(r"function\s+(test\w+)\s*\("), (1,)),
            (re.compile(r"(?<![\w>$])(?:it|test)\s*\(\s*(['\"])(.{0,300}?)\1"), (2,))],
    "swift": [(re.compile(r"func\s+(test\w+)\s*\("), (1,)),
              (re.compile(r"@Test\b[^\n]*\n?\s*(?:@\w+\s+)*func\s+(\w+)"), (1,))],
    "dart": [(re.compile(B + r"(?:test|testWidgets|testGoldens)\s*\(\s*(['\"])(.{0,300}?)\1"), (2,))],
    "elixir": [(re.compile(r"^[ \t]*test[ \t]+\"((?:[^\"\\]|\\.){0,300})\"", re.M), (1,))],
    "cpp": [(re.compile(r"\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)"), (1, 2)),
            (re.compile(r"\bTEST_CASE\s*\(\s*\"([^\"]*)\""), (1,)),
            (re.compile(r"\bBOOST_AUTO_TEST_CASE\s*\(\s*(\w+)"), (1,))],
    "shell": [(re.compile(r"@test\s+(['\"])(.{0,300}?)\1"), (2,))],
}

# --------------------------------------------------------------------------- try/catch swallowing

EMPTY_CATCH_INLINE = _any(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?:/\*.*?\*/\s*)?\}",
    r"\.catch\s*\(\s*(?:\(\s*\w*\s*\)|\w+)\s*=>\s*(?:\{\s*\}|null|undefined|void\s+0|\[\]|\{\s*\}|false)\s*\)",
    r"\.catch\s*\(\s*(?:noop|_\.noop|\(\)\s*=>\s*\{\s*\})\s*\)",
    r"^\s*except\s*(?:\(?\s*(?:Exception|BaseException)(?:\s*,\s*\w+)*\s*\)?)?\s*(?:as\s+\w+)?\s*:\s*(?:pass|\.\.\.|continue)\s*(?:#.*)?$",
    r"\bsuppress\(\s*(?:Exception|BaseException)\s*\)",
    r"\brescue\s*(?:=>\s*\w+)?\s*nil\b|\s+rescue\s+nil\s*$",
    r"\bif\s+err\s*!=\s*nil\s*\{\s*\}",
    r"^\s*_\s*=\s*err\s*$",
    r"\bcatch\s*\(\s*(?:Exception|Throwable|\w*Exception)\s+\w+\s*\)\s*\{\s*\}",
    r"\bcatch\s*\{\s*\}",
    r"\bOn\s+Error\s+Resume\s+Next\b",
)
CATCH_OPEN = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?://.*)?$")
EXCEPT_OPEN = re.compile(r"^\s*except\b[^:]*:\s*(?:#.*)?$")
BLOCK_CLOSE = re.compile(r"^\s*\}\s*[);,]*\s*$")
SWALLOW_BODY = re.compile(r"^\s*(?:pass|\.\.\.|continue|return\s*(?:None)?)\s*(?:#.*)?$")
EXCEPT_TYPES = re.compile(r"^\s*except\b\*?\s*(?P<types>[^:]*?)\s*(?:\bas\s+\w+)?\s*:")
BROAD_EXCEPTIONS = {"Exception", "BaseException", "AssertionError"}


def broad_except(text: str) -> bool:
    """`except:`, `except Exception` or anything naming AssertionError, as opposed to specific error types."""
    m = EXCEPT_TYPES.match(text)
    if not m:
        return False
    names = re.findall(r"[A-Za-z_][\w.]*", m.group("types"))
    return not names or any(n.rsplit(".", 1)[-1] in BROAD_EXCEPTIONS for n in names)

# --------------------------------------------------------------------------- suppressions

SUPPRESS_FILE_LEVEL = _any(
    r"@ts-nocheck\b", r"/\*\s*eslint-disable\s*\*/", r"^\s*/\*\s*eslint-disable(?!-)", r"^\s*//\s*eslint-disable(?!-)\s*$",
    r"#\s*mypy:\s*ignore-errors\b", r"#\s*ruff:\s*noqa\s*$", r"#\s*flake8:\s*noqa\s*$", r"#\s*pyright:\s*basic\b",
    r"#!\[\s*allow\s*\(\s*warnings", r"//\s*swiftlint:disable\s+all\b", r"#\s*rubocop:disable\s+all\b",
)
SUPPRESS_LINE = _any(
    r"@ts-ignore\b", r"@ts-expect-error\b", r"eslint-disable-(?:next-)?line\b", r"\bbiome-ignore\b", r"\boxlint-ignore\b",
    r"#\s*type:\s*ignore\b", r"#\s*pyright:\s*ignore\b", r"#\s*noqa\b", r"#\s*pylint:\s*disable\b",
    r"//\s*nolint\b", r"#\[\s*allow\s*\(", r"@SuppressWarnings\b", r"@Suppress\s*\(", r"//\s*NOSONAR\b",
    r"#pragma\s+warning\s+disable\b", r"\[SuppressMessage\b", r"#\s*rubocop:disable\b", r"@phpstan-ignore",
    r"@psalm-suppress\b", r"//\s*swiftlint:disable\b",
)
TS_ESCAPE_HATCH = _any(r"\bas\s+any\b", r"\bas\s+unknown\s+as\b", r"<any>\s*\w")
COVERAGE_IGNORE = _any(
    r"\bistanbul\s+ignore\b", r"\bc8\s+ignore\b", r"\bv8\s+ignore\b", r"#\s*pragma:\s*no\s*cover\b",
    r"#\[\s*coverage\s*\(\s*off", r"@codeCoverageIgnore\b", r"\[ExcludeFromCodeCoverage\b", r"\bLCOV_EXCL",
    r"//\s*coverage:ignore\b", r"#\s*nocov\b",
)

# --------------------------------------------------------------------------- production code red flags

TEST_ENV_STRONG = _any(
    r"process\.env\.(?:JEST_WORKER_ID|VITEST(?:_WORKER_ID|_POOL_ID)?|MOCHA_\w+|PLAYWRIGHT_\w+)\b",
    r"import\.meta\.env\.VITEST\b",
    r"typeof\s+(?:jest|vi|describe|it|expect)\s*!==?\s*['\"]undefined['\"]",
    r"globalThis\.(?:jest|vi|__vitest\w*)\b",
    r"['\"](?:pytest|_pytest|unittest)['\"]\s+in\s+sys\.modules",
    r"PYTEST_CURRENT_TEST|PYTEST_VERSION|PYTEST_XDIST_WORKER",
    r"sys\.argv\b.*\bpytest\b",
    r"\btesting\.Testing\(\)",
    r"flag\.Lookup\(\s*\"test\.v\"\s*\)",
    r"strings\.HasSuffix\(\s*os\.Args\[0\]\s*,\s*\"\.test\"\s*\)",
    r"Thread\.currentThread\(\)\.getStackTrace\(\)[\s\S]{0,80}(?:junit|testng)",
    r"getProperty\(\s*\"(?:junit|surefire)\.",
    r"GetAssemblies\(\)[\s\S]{0,80}(?:xunit|nunit|mstest)",
    r"defined\?\s*\(\s*(?:RSpec|Minitest)\s*\)",
    r"defined\(\s*['\"]PHPUNIT\w*['\"]\s*\)|->runningUnitTests\(\)",
)
TEST_ENV_WEAK = _any(
    r"process\.env\.NODE_ENV\s*[!=]==?\s*['\"]test['\"]",
    r"['\"]test['\"]\s*[!=]==?\s*process\.env\.NODE_ENV",
    r"import\.meta\.env\.MODE\s*[!=]==?\s*['\"]test['\"]",
    r"os\.(?:environ\.get|getenv)\(\s*['\"](?:TESTING|TEST_MODE|IS_TEST|UNIT_TEST\w*)['\"]",
    r"os\.environ\[\s*['\"](?:TESTING|TEST_MODE|IS_TEST)['\"]\s*\]",
    r"\bRails\.env\.test\?",
    r"\bcfg!\(\s*test\s*\)",
    r"\b(?:IS_TEST|TEST_MODE|isTestEnv|isTestEnvironment|isRunningTests?|inTestMode|IN_TEST)\b",
    r"\bapp\(\)->environment\(\s*['\"]testing['\"]\s*\)",
)

PLACEHOLDER_STRONG = re.compile(
    r"(?i)(?:\b(?:to|so\s+that|so)\s+(?:make|get)\s+(?:the\s+)?(?:failing\s+)?tests?\s+(?:to\s+)?pass"
    r"|\bhard-?coded?\s+(?:for|to\s+(?:pass|satisfy|match))\s+(?:the\s+)?tests?"
    r"|\b(?:special[- ]?case|workaround)\s+for\s+(?:the\s+)?tests?\b"
    r"|\bfake\s+(?:it\s+)?(?:for|to\s+pass)\s+(?:the\s+)?tests?"
    r"|\bmatch(?:es)?\s+(?:the\s+)?expected\s+test\s+output)"
)
PLACEHOLDER = re.compile(
    r"(?i)(?:\bin\s+a\s+real\s+(?:implementation|app(?:lication)?|scenario|system|world|project|codebase)"
    r"|\bin\s+production,?\s+(?:this|you|we)\s+would"
    r"|\bthis\s+(?:is|would\s+be)\s+(?:just\s+)?a\s+(?:placeholder|stub|mock|simplified|dummy|fake)\b"
    r"|\b(?:placeholder|stub(?:bed)?|mock|fake|dummy)\s+(?:implementation|logic|response|data|value)s?\b"
    r"|\bsimplified\s+(?:version|implementation|logic|approach)\b"
    r"|\bwould\s+(?:normally|typically)\s+(?:call|fetch|query|connect|be|use|validate|compute|parse)\b"
    r"|\b(?:TODO|FIXME)\s*:?\s*(?:actually\s+)?implement\b"
    r"|\bnot\s+(?:yet\s+)?implemented\b"
    r"|\bimplement\s+(?:this\s+)?(?:properly|later|for\s+real)\b"
    r"|\bfor\s+now,?\s+(?:just\s+)?(?:return|hard-?code|skip|ignore|assume|mock|stub)\b)"
)
NOT_IMPLEMENTED_CODE = _any(
    r"\braise\s+NotImplementedError\b",
    r"\bthrow\s+new\s+(?:Error|NotImplementedException|UnsupportedOperationException)\s*\(\s*['\"`]?[^)]*(?:not\s+implemented|todo|unimplemented)",
    r"\btodo!\s*\(|\bunimplemented!\s*\(",
    r"\bpanic\(\s*\"(?:not\s+implemented|todo|unimplemented)",
    r"\bthrow\s+new\s+NotImplementedException\b",
    r"\bfatalError\(\s*\"(?:not\s+implemented|todo|unimplemented)",
    flags=re.I,
)

# --------------------------------------------------------------------------- literal helpers

STRING_LIT = r"(?:\"(?:[^\"\\\n]|\\.){0,200}\"|'(?:[^'\\\n]|\\.){0,200}'|`[^`\n]{0,200}`)"
NUMBER_LIT = r"(?<![\w.])-?\d+(?:\.\d+)?(?:e[+-]?\d+)?(?![\w.])"
LIT = "(?:%s|%s)" % (STRING_LIT, NUMBER_LIT)

CONDITIONAL = re.compile(r"\b(?:if|elif|elsif|when|case|unless|switch|match|guard)\b|\?|&&|\|\|")
COMPARE_LIT = re.compile(
    r"(?:===?|\bis\b(?!\s+not)|\beq\b)\s*(?P<a>" + LIT + r")|(?P<b>" + LIT + r")\s*(?:===?|\beq\b)"
)
CASE_LIT = re.compile(
    r"(?:^|[\s(,])(?:case|when)\s+(?P<a>" + LIT + r")\s*(?:[:,]|then\b|->|=>|$)|^\s*(?P<b>" + LIT + r")\s*(?:=>|->)"
)
RETURN_LIT = re.compile(
    r"(?:\breturn\b\s*\(?\s*|=>\s*\(?\s*|->\s*|\byield\s+|\bthen\s+|\bdo:\s*)(?P<v>" + LIT + r")\s*\)?\s*[;,)}\]]?\s*(?:#.*|//.*)?$"
)
# `cond ? 90 : other` and Python's `90 if cond else other`, on the line holding the condition.
TERNARY_LIT = re.compile(r"\?\s*(?P<v>" + LIT + r")\s*:(?!:)")
PY_COND_LIT = re.compile(r"(?P<v>" + LIT + r")\s+if\b.*\belse\b")
# `{"SAVE10": 90}` / `"SAVE10" => 90` entries, and `("SAVE10", 90)` pairs that are elements of a list.
DICT_PAIR = re.compile(r"(?<!\?)(?<!\?\s)(?P<k>" + LIT + r")\s*(?::|=>)\s*(?P<v>" + LIT + r")")  # not `? a : b`
TUPLE_PAIR = re.compile(
    r"(?:^|[\[,{]|(?<![\w\])])\()\s*[\[({]\s*(?P<k>" + LIT + r")\s*,\s*(?P<v>" + LIT + r")\s*[\])}]"  # not `f(["a", "b"])`
)

TRIVIAL_LITERALS = {
    "", "0", "1", "-1", "2", "true", "false", "null", "none", "nil", "undefined", "ok", "id", "name", "type",
    "value", "key", "data", "error", "errors", "success", "get", "post", "put", "patch", "delete", "head",
    "options", "utf-8", "utf8", "json", "text", "string", "number", "object", "boolean", "default", "test",
    "en", "us", "yes", "no", "on", "off", "a", "b", "x", "y", "n", "/", ".", ",", ":", " ", "-", "_", "*",
    "\\n", "content-type", "application/json", "200", "201", "204", "400", "401", "403", "404", "500",
}


def literal_value(lit: str) -> str:
    lit = lit.strip()
    if len(lit) >= 2 and lit[0] in "\"'`" and lit[-1] == lit[0]:
        return lit[1:-1]
    try:
        f = float(lit)
        return repr(int(f)) if f == int(f) and "e" not in lit.lower() else repr(f)
    except ValueError:
        return lit


def is_notable(value: str) -> bool:
    v = value.strip().lower()
    if v in TRIVIAL_LITERALS:
        return False
    try:
        num = float(v)
        return abs(num) >= 11 or (num != int(num))
    except ValueError:
        return len(v) >= 3


def literals_in(text: str) -> List[str]:
    return [literal_value(m.group(0)) for m in re.finditer(LIT, text)]


_TOKEN = re.compile(r"(?P<lit>" + LIT + r")|(?P<ident>[A-Za-z_$][\w$]*)|(?P<open>[\[({])|(?P<close>[\])}])|(?P<other>\S)")
_NOT_CALLEES = {"in", "of", "return", "if", "elif", "while", "for", "and", "or", "not", "is", "yield", "await",
                "assert", "else", "case", "when", "lambda", "throw", "typeof"}
_ASSIGNED = re.compile(r"(?<![=!<>:+\-*/%&|^~])=\s*$")
_COMPARED = re.compile(r"(?:={2,3}|!==?|\bis|\beq)\s*$")
_COMPARED_AFTER = re.compile(r"\s*(?:={2,3}|!==?)")
_EXPECTED_NAME = re.compile(r"(?i)\b(?:want|wants|expected?|exp|output|out|result|answer)\w*\s*[:=]\s*$")
_EXPECT_CALLS = {
    "toEqual", "toBe", "toStrictEqual", "toBeCloseTo", "toMatch", "toContain", "toMatchObject", "toHaveLength",
    "assertEqual", "assertEquals", "assertAlmostEqual", "assertIs", "assertIn", "assertSame", "assertThat",
    "assert_eq", "assert_ne", "assert_approx_eq", "equal", "equals", "deepEqual", "strictEqual", "deepStrictEqual",
    "eql", "eq", "Equal", "EqualValues", "Exactly", "InDelta", "is_equal_to", "isEqualTo", "approx", "same",
}


class _Brackets:
    """The open brackets at each literal of one line: calls, tuples, lists, subscripts, braces and struct literals."""

    def __init__(self, text: str):
        self.text = text
        self.first = len(text) - len(text.lstrip())

    def literals(self):
        """Yield (literal match, bracket stack, previous token) for every literal on the line."""
        stack: List[Tuple[str, int, str]] = []  # (kind, column, callee)
        prev_kind, prev_tok, before_prev = "", "", ""
        for m in _TOKEN.finditer(self.text):
            kind, tok = m.lastgroup or "", m.group(0)
            if kind == "open":
                callee = prev_tok if prev_tok != "!" else before_prev  # Rust macros: assert_eq!(...)
                follows_value = (prev_kind in ("ident", "close", "lit") or prev_tok == "!") and callee not in _NOT_CALLEES
                if tok == "(":
                    stack.append(("call" if follows_value else "tuple", m.start(), callee if follows_value else ""))
                elif tok == "[":
                    stack.append(("index" if follows_value else "list", m.start(), ""))
                else:
                    struct = prev_kind == "ident" and prev_tok not in _NOT_CALLEES
                    stack.append(("struct" if struct else "brace", m.start(), ""))
            elif kind == "close":
                if stack:
                    stack.pop()
            elif kind == "lit":
                yield m, stack, prev_tok
            before_prev = prev_tok
            prev_kind, prev_tok = kind, tok

    def is_row(self, stack: List[Tuple[str, int, str]]) -> bool:
        """Inside one row of a table: `("a=1", 1)`, `[["SAVE10", 90]]`, Go's `{"SAVE10", 90},` on its own line."""
        kind, column, _ = stack[-1]
        parent = stack[-2][0] if len(stack) > 1 else None
        if kind in ("tuple", "struct"):
            return True
        if kind in ("list", "brace"):
            return (parent is None and column == self.first) or parent in ("list", "tuple", "struct")
        return False


def _is_key(text: str, m: "re.Match[str]") -> bool:
    after = text[m.end():].lstrip()
    return after.startswith(":") and not after.startswith("::")


def input_literals(text: str) -> List[str]:
    """Literals a test line passes in: call arguments, table-row entries and assigned values.

    Collections (`.isin(["refund", "late"])`, `{"a", "b"}`), subscripts (`row["kind"]`), keys (`"kind": ...`) and
    compared values (`== "x"`) are left out, so records and option lists that production code shares with test
    fixtures do not look like answers copied from the tests.
    """
    line = _Brackets(text)
    out: List[str] = []
    for m, stack, prev_tok in line.literals():
        if _is_key(text, m):
            continue
        if not stack:
            ok = bool(_ASSIGNED.search(text[:m.start()]))
        elif stack[-1][0] == "call":
            ok = True
        elif stack[-1][0] == "brace" and len(stack) > 1 and stack[-2][0] == "call" and prev_tok == ":":
            ok = True  # an options object passed to the code under test: total({code: "SAVE10"})
        else:
            ok = line.is_row(stack)
        if ok:
            out.append(literal_value(m.group(0)))
    return out


def expected_literals(text: str) -> List[str]:
    """Literals a test line expects: compared with `==`, passed to toEqual/assertEqual/assert_eq!, a table-row
    entry, or the value of a `want` / `expected` field or variable."""
    line = _Brackets(text)
    out: List[str] = []
    for m, stack, _ in line.literals():
        if _is_key(text, m):
            continue
        before = text[:m.start()]
        ok = bool(_COMPARED.search(before) or _COMPARED_AFTER.match(text, m.end()) or _EXPECTED_NAME.search(before))
        if not ok and stack:
            ok = (stack[-1][0] == "call" and stack[-1][2] in _EXPECT_CALLS) or line.is_row(stack)
        if ok:
            out.append(literal_value(m.group(0)))
    return out


def family(lang: str) -> Optional[str]:
    if lang in SKIP or lang in ASSERT:
        return lang
    return None
