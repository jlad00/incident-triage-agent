"""Unit tests for ResponseParser."""

import json
import pytest
from agent.llm.response_parser import ResponseParser, ParseError


@pytest.fixture
def parser():
    return ResponseParser()


@pytest.fixture
def valid_response():
    return json.dumps({
        "summary": "Payment service experienced a critical failure following deployment of v2.4.1. The update caused connection pool exhaustion and circuit breaker activation, resulting in cascading 503 errors for API consumers.",
        "hypotheses": [
            {
                "rank": 1,
                "hypothesis": "Deployment v2.4.1 introduced a regression in DB connection pool configuration causing pool exhaustion",
                "confidence": "high",
                "evidence": [
                    "connection_pool_exhausted signal detected 2m 8s after deployment",
                    "circuit_breaker_open triggered on db-primary",
                    "HIGH correlation between deployment and first signals"
                ],
                "reasoning": "The tight temporal correlation between the deployment and the connection pool exhaustion strongly suggests a regression. The circuit breaker opening confirms the database became unreachable at the application level."
            }
        ],
        "next_steps": [
            {
                "priority": 1,
                "action": "Check current DB connection pool utilization: kubectl exec -it <pod> -- psql -c 'SELECT count(*) FROM pg_stat_activity'",
                "rationale": "Confirms whether the pool is still exhausted or has recovered, and reveals active connection count."
            },
            {
                "priority": 2,
                "action": "Compare connection pool config between v2.4.0 and v2.4.1 in the deployment diff",
                "rationale": "Directly identifies whether the pool size or timeout settings were changed in the bad deploy."
            }
        ],
        "remediation_suggestions": [
            {
                "action": "Roll back payment-service to v2.4.0",
                "condition": "If DB connection pool remains exhausted and rollback is confirmed safe"
            }
        ],
        "severity_assessment": {
            "estimate": "P1",
            "agrees_with_computed": True,
            "reasoning": "Error rate >80% with circuit breaker open warrants P1. Computed estimate is correct."
        },
        "confidence_note": "High confidence given the tight temporal correlation with the deployment and the presence of both connection pool exhaustion and circuit breaker signals."
    })


class TestResponseParser:
    def test_parses_valid_response(self, parser, valid_response):
        report = parser.parse(valid_response, incident_id="test-01")
        assert report.summary != ""
        assert len(report.hypotheses) == 1
        assert len(report.next_steps) == 2

    def test_sets_incident_id(self, parser, valid_response):
        report = parser.parse(valid_response, incident_id="abc123")
        assert report.incident_id == "abc123"

    def test_top_hypothesis_is_rank_1(self, parser, valid_response):
        report = parser.parse(valid_response)
        assert report.top_hypothesis.rank == 1

    def test_severity_is_p1(self, parser, valid_response):
        report = parser.parse(valid_response)
        assert report.severity == "P1"

    def test_strips_markdown_fences(self, parser, valid_response):
        fenced = f"```json\n{valid_response}\n```"
        report = parser.parse(fenced)
        assert report.summary != ""

    def test_strips_bare_fences(self, parser, valid_response):
        fenced = f"```\n{valid_response}\n```"
        report = parser.parse(fenced)
        assert report.summary != ""

    def test_raises_on_invalid_json(self, parser):
        with pytest.raises(ParseError, match="not valid JSON"):
            parser.parse("this is not json at all")

    def test_raises_on_missing_summary(self, parser, valid_response):
        data = json.loads(valid_response)
        del data["summary"]
        with pytest.raises(ParseError, match="missing required fields"):
            parser.parse(json.dumps(data))

    def test_raises_on_missing_hypotheses(self, parser, valid_response):
        data = json.loads(valid_response)
        del data["hypotheses"]
        with pytest.raises(ParseError, match="missing required fields"):
            parser.parse(json.dumps(data))

    def test_raises_on_empty_hypotheses(self, parser, valid_response):
        data = json.loads(valid_response)
        data["hypotheses"] = []
        with pytest.raises(ParseError, match="at least one hypothesis"):
            parser.parse(json.dumps(data))

    def test_confidence_normalized_to_lowercase(self, parser, valid_response):
        data = json.loads(valid_response)
        data["hypotheses"][0]["confidence"] = "HIGH"
        report = parser.parse(json.dumps(data))
        assert report.hypotheses[0].confidence == "high"

    def test_severity_normalized_to_uppercase(self, parser, valid_response):
        data = json.loads(valid_response)
        data["severity_assessment"]["estimate"] = "p2"
        report = parser.parse(json.dumps(data))
        assert report.severity == "P2"

    def test_next_steps_sorted_by_priority(self, parser, valid_response):
        data = json.loads(valid_response)
        data["next_steps"] = [
            {"priority": 3, "action": "c", "rationale": "r"},
            {"priority": 1, "action": "a", "rationale": "r"},
            {"priority": 2, "action": "b", "rationale": "r"},
        ]
        report = parser.parse(json.dumps(data))
        priorities = [s.priority for s in report.next_steps]
        assert priorities == [1, 2, 3]