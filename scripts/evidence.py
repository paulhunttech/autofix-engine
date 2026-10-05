#!/usr/bin/env python3
"""Gather the telemetry a fix needs, with fixed engine-owned KQL.

Runs in job `evidence`, which holds only an Azure OIDC login: no model, no Anthropic
key, no GitHub credential. The model never runs a query. It reads the evidence.json
this script writes, so no query-side channel (externaldata and the like) is in its
reach.

The queries project only the columns a fix needs. Client IP, user and session ids,
and the free-form Properties bags are never selected, which bounds what any later
egress could carry.

Usage: evidence.py <out evidence.json>
Reads INPUT_PROBLEM_ID, INPUT_WINDOW_START, INPUT_WINDOW_END and EVIDENCE_WORKSPACE
from the environment. Writes counts only to the job log and summary.
"""

import json
import os
import subprocess
import sys
import urllib.request

QUERY_URL = "https://api.loganalytics.azure.com/v1/workspaces/{workspace}/query"
TOKEN_RESOURCE = "https://api.loganalytics.io"

MAX_EXCEPTIONS = 50
MAX_DEPENDENCIES = 50
MAX_REQUESTS = 50
MAX_OPERATIONS = 50
MAX_CELL_CHARS = 4000
MAX_TOTAL_CHARS = 200_000
HTTP_TIMEOUT_S = 120

EXCEPTION_COLUMNS = (
    "TimeGenerated, ExceptionType, OuterType, InnermostType, Method, OuterMethod, InnermostMethod, "
    "OuterMessage, InnermostMessage, Details, OperationId, OperationName, AppRoleName, AppVersion"
)


def kql_string(value):
    """A KQL verbatim string literal: @"..." with every " doubled. Nothing else is special in it."""
    if "\n" in value or "\r" in value:
        raise ValueError("a KQL verbatim literal cannot carry a newline")
    return '@"' + value.replace('"', '""') + '"'


def build_queries(problem_id):
    """The four fixed queries, keyed by section. The problem id enters only as one literal."""
    pid = kql_string(problem_id)
    ops = (
        f"let ops = AppExceptions | where ProblemId == {pid} "
        f"| distinct OperationId | take {MAX_OPERATIONS};\n"
    )
    return {
        "exceptions": (
            f"AppExceptions\n"
            f"| where ProblemId == {pid}\n"
            f"| project {EXCEPTION_COLUMNS}\n"
            f"| order by TimeGenerated desc\n"
            f"| take {MAX_EXCEPTIONS}"
        ),
        "count_30d": (
            f"AppExceptions\n"
            f"| where ProblemId == {pid}\n"
            f"| summarize count_30d = sum(ItemCount)"
        ),
        "failed_dependencies": (
            ops
            + "AppDependencies\n"
            "| where OperationId in (ops) and Success == false\n"
            # Data is a full URI or SQL text. For HTTP calls everything from the first "?" is dropped:
            # a query string can carry personal data, as a request Url can (which is why Url isn't
            # projected). SQL text is kept whole, "?" placeholders included, because the statement is
            # often what a fix needs. `startswith` is case-insensitive in KQL.
            '| extend Data = iff(DependencyType startswith "http", tostring(split(Data, "?")[0]), Data)\n'
            "| project TimeGenerated, OperationId, DependencyType, Target, Name, Data, ResultCode, DurationMs\n"
            "| order by TimeGenerated desc\n"
            f"| take {MAX_DEPENDENCIES}"
        ),
        "failed_requests": (
            ops
            + "AppRequests\n"
            "| where OperationId in (ops) and Success == false\n"
            "| project TimeGenerated, OperationId, Name, ResultCode, DurationMs, AppRoleName\n"
            "| order by TimeGenerated desc\n"
            f"| take {MAX_REQUESTS}"
        ),
    }


def _cap(value):
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "…[truncated]", True
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) > MAX_CELL_CHARS:
            return text[:MAX_CELL_CHARS] + "…[truncated]", True
    return value, False


def rows_of(response):
    """Turn a Logs query API response into a list of {column: value} dicts, with cells capped."""
    tables = response.get("tables") or []
    if not tables:
        return [], False
    table = tables[0]
    names = [c["name"] for c in table.get("columns", [])]
    out, truncated = [], False
    for row in table.get("rows", []):
        record = {}
        for name, cell in zip(names, row):
            capped, cut = _cap(cell)
            truncated = truncated or cut
            record[name] = capped
        out.append(record)
    return out, truncated


def assemble(problem_id, window_start, window_end, results):
    """Build the evidence document from per-section query results, enforcing the total cap."""
    exceptions, t1 = rows_of(results["exceptions"])
    deps, t2 = rows_of(results["failed_dependencies"])
    reqs, t3 = rows_of(results["failed_requests"])
    count_rows, _ = rows_of(results["count_30d"])
    count = count_rows[0].get("count_30d") if count_rows else 0
    doc = {
        "problem_id": problem_id,
        "window": {"start": window_start, "end": window_end},
        "count_30d": count or 0,
        "exceptions": exceptions,
        "failed_dependencies": deps,
        "failed_requests": reqs,
        "truncated": t1 or t2 or t3,
    }
    # Drop the oldest rows of the largest section until the whole document fits.
    while len(json.dumps(doc, ensure_ascii=False)) > MAX_TOTAL_CHARS:
        section = max(("exceptions", "failed_dependencies", "failed_requests"), key=lambda k: len(doc[k]))
        if not doc[section]:
            break
        doc[section].pop()
        doc["truncated"] = True
    return doc


def _token():
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource", TOKEN_RESOURCE, "--query", "accessToken", "-o", "tsv"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def run_query(workspace, token, query, timespan):
    body = json.dumps({"query": query, "timespan": timespan}).encode("utf-8")
    req = urllib.request.Request(
        QUERY_URL.format(workspace=workspace),
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.load(resp)


def main(argv):
    if len(argv) != 2:
        print("usage: evidence.py <out evidence.json>", file=sys.stderr)
        return 2
    problem_id = os.environ["INPUT_PROBLEM_ID"]
    start, end = os.environ["INPUT_WINDOW_START"], os.environ["INPUT_WINDOW_END"]
    workspace = os.environ["EVIDENCE_WORKSPACE"]

    token = _token()
    window = f"{start}/{end}"
    timespans = {"exceptions": window, "count_30d": "P30D", "failed_dependencies": window, "failed_requests": window}
    results = {}
    for section, query in build_queries(problem_id).items():
        try:
            results[section] = run_query(workspace, token, query, timespans[section])
        except Exception as e:  # never print the response body: it can carry telemetry
            print(f"::error::query '{section}' failed ({type(e).__name__})")
            return 1

    doc = assemble(problem_id, start, end, results)
    with open(argv[1], "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    counts = (
        f"exceptions={len(doc['exceptions'])} failed_dependencies={len(doc['failed_dependencies'])} "
        f"failed_requests={len(doc['failed_requests'])} count_30d={doc['count_30d']} truncated={doc['truncated']}"
    )
    print(counts)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"**evidence**: {counts}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
