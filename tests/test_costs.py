"""成本台账高风险路径：幂等写入 / 非 0 记账 / 会话聚合。"""
from sparring.costs import CostLedger


def _record(ledger, rid="req1", session="s1", cost=0.0123):
    ledger.record(
        request_id=rid,
        role="contributor",
        model="m",
        tokens_in=1000,
        tokens_out=500,
        cost_usd=cost,
        session_id=session,
    )


def test_record_nonzero_and_total(tmp_path):
    ledger = CostLedger(tmp_path / "t.db")
    _record(ledger)
    total, n = ledger.session_total("s1")
    assert n == 1
    assert total > 0


def test_record_idempotent_same_request_id(tmp_path):
    ledger = CostLedger(tmp_path / "t.db")
    _record(ledger, rid="dup")
    _record(ledger, rid="dup")
    total, n = ledger.session_total("s1")
    assert n == 1


def test_session_isolation(tmp_path):
    ledger = CostLedger(tmp_path / "t.db")
    _record(ledger, rid="r1", session="s1")
    _record(ledger, rid="r2", session="s2", cost=1.0)
    total_s1, _ = ledger.session_total("s1")
    assert abs(total_s1 - 0.0123) < 1e-9
    assert ledger.grand_total() > 1.0
