"""evidence.py: the problem id enters the KQL as exactly one verbatim literal, and the KQL is pinned whole."""

import json
import re

import pytest

import evidence
from evidence import assemble, build_queries, kql_string, rows_of

# A KQL verbatim literal @"..." ends at the first " that is not doubled.
VERBATIM_RE = re.compile(r'@"(?:[^"]|"")*"')


def literals(query):
    return VERBATIM_RE.findall(query)


@pytest.mark.parametrize("pid", [
    'a"b',
    'a\\b',
    'a|b',
    'a;b',
    '"; AppExceptions | take 1 | externaldata(x:string) [@"https://evil"]; //',
    '\\" | union *',
    '""""',
])
def test_hostile_problem_id_is_one_literal(pid):
    """Mutant killed: escaping with \\" (a verbatim literal treats \\ as literal) or no escaping at all."""
    lit = kql_string(pid)
    assert VERBATIM_RE.fullmatch(lit), "the literal must be a single verbatim token"
    assert lit[2:-1].replace('""', '"') == pid, "the literal must decode back to the input exactly"
    for query in build_queries(pid).values():
        found = literals(query)
        assert found and all(f == lit for f in found), "the id must appear only as the one literal"
        # With the literal removed, nothing of the hostile text remains in the query.
        assert "externaldata" not in VERBATIM_RE.sub("", query)
        assert "union" not in VERBATIM_RE.sub("", query)


def test_newline_refused():
    with pytest.raises(ValueError):
        kql_string("a\nb")


PINNED = {
    "exceptions": (
        'AppExceptions\n'
        '| where ProblemId == @"P"\n'
        '| project TimeGenerated, ExceptionType, OuterType, InnermostType, Method, OuterMethod, InnermostMethod, '
        'OuterMessage, InnermostMessage, Details, OperationId, OperationName, AppRoleName, AppVersion\n'
        '| order by TimeGenerated desc\n'
        '| take 50'
    ),
    "count_30d": (
        'AppExceptions\n'
        '| where ProblemId == @"P"\n'
        '| summarize count_30d = sum(ItemCount)'
    ),
    "failed_dependencies": (
        'let ops = AppExceptions | where ProblemId == @"P" | distinct OperationId | take 50;\n'
        'AppDependencies\n'
        '| where OperationId in (ops) and Success == false\n'
        '| extend Data = iff(DependencyType startswith "http", tostring(split(Data, "?")[0]), Data)\n'
        '| project TimeGenerated, OperationId, DependencyType, Target, Name, Data, ResultCode, DurationMs\n'
        '| order by TimeGenerated desc\n'
        '| take 50'
    ),
    "failed_requests": (
        'let ops = AppExceptions | where ProblemId == @"P" | distinct OperationId | take 50;\n'
        'AppRequests\n'
        '| where OperationId in (ops) and Success == false\n'
        '| project TimeGenerated, OperationId, Name, ResultCode, DurationMs, AppRoleName\n'
        '| order by TimeGenerated desc\n'
        '| take 50'
    ),
}


def test_kql_pinned_whole():
    """Mutant killed: any change to a query, including widening a projection."""
    assert build_queries("P") == PINNED


@pytest.mark.parametrize("column", ["ClientIP", "UserId", "SessionId", "Properties", "UserAuthenticatedId", "Url", "Measurements"])
def test_personal_columns_never_projected(column):
    for query in build_queries("P").values():
        assert column not in query


def _table(columns, rows):
    return {"tables": [{"name": "PrimaryResult", "columns": [{"name": c, "type": "string"} for c in columns], "rows": rows}]}


def test_cells_capped():
    rows, truncated = rows_of(_table(["Details"], [["x" * (evidence.MAX_CELL_CHARS + 10)]]))
    assert truncated and len(rows[0]["Details"]) < evidence.MAX_CELL_CHARS + 20


def test_assemble_enforces_total_cap(monkeypatch):
    monkeypatch.setattr(evidence, "MAX_TOTAL_CHARS", 2000)
    big = _table(["OuterMessage"], [["m" * 300] for _ in range(20)])
    doc = assemble("P", "s", "e", {
        "exceptions": big,
        "failed_dependencies": _table(["Name"], []),
        "failed_requests": _table(["Name"], []),
        "count_30d": _table(["count_30d"], [[123]]),
    })
    assert len(json.dumps(doc, ensure_ascii=False)) <= 2000
    assert doc["truncated"] and doc["count_30d"] == 123
