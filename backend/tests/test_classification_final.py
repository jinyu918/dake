from copy import deepcopy
from dataclasses import replace

import pytest

from final_replay import Replay, attribute, common, fact, record
from offline_http import offline_only
from test_classification_replay import change, day_window, msg, ref


CASES = [
    ("A03", "我们决定为A项目新增采购B服务，请报一年方案价。", ["商机", "商务需求"], [
        ("商机", "交易对象与采购范围", "为A项目新增采购B服务", "采购意向"),
        ("商务需求", "合同与商务条件", "一年", "报价期限")]),
    ("A04", "先给A环境开个试用配额，测试一下是否合适。", ["资源需求"], [
        ("资源需求", "请求动作与用途", "开个试用配额", "技术试用")]),
    ("A08", "请提高A环境配额，具体数值还在测算。", ["资源需求"], [
        ("资源需求", "请求动作与用途", "提高A环境配额", "申请")]),
    ("A11", "A项目上线前请按这份使用条件核验现有B服务适不适用，给我们书面结论。", ["其他"], []),
    ("A12", "A导出支持取消吗？若还不支持，请增加取消任务能力，避免长任务误跑。", ["功能需求"], [
        ("功能需求", "期望行为与改善目标", "若还不支持，请增加取消任务能力", "条件性请求")]),
    ("A13", "A导出目前正常，但想把多步操作合成一键，请改善。", ["功能需求"], [
        ("功能需求", "当前行为及限制", "目前正常", "现状"),
        ("功能需求", "期望行为与改善目标", "把多步操作合成一键", "请求")]),
    ("A14", "A导出原来能取消，今天取消按钮失效，请恢复。", ["排障"], [
        ("排障", "症状与对照表现", "原来能取消，今天取消按钮失效", "客户报告")]),
    ("A15", "A接口平时200毫秒，今天同样请求要5秒，请排查。", ["排障"], [
        ("排障", "症状与对照表现", "平时200毫秒，今天同样请求要5秒", "客户报告")]),
    ("A16", "现在性能符合约定，为活动请换到现有大规格实例，以满足更高吞吐。", ["资源需求"], [
        ("资源需求", "资源对象与交付范围", "现有大规格实例", "申请"),
        ("资源需求", "请求动作与用途", "为活动请换到现有大规格实例", "申请")]),
    ("A17", "现有搜索功能符合标准，但希望产品在同样资源条件下支持10万条实时检索，目前没有这项能力。", ["功能需求"], [
        ("功能需求", "期望行为与改善目标", "在同样资源条件下支持10万条实时检索", "请求"),
        ("功能需求", "当前行为及限制", "目前没有这项能力", "客户陈述")]),
    ("A21", "合同C下月续费，请发续费合同办理。", ["商务需求"], [
        ("商务需求", "续费或终止安排", "合同C下月续费", "续费请求")]),
    ("A22", "合同C决定不续约，请办理本期到期终止手续。", ["流失", "商务需求"], [
        ("流失", "风险、计划与实际变化", "决定不续约", "不续约计划"),
        ("商务需求", "续费或终止安排", "办理本期到期终止手续", "终止请求")]),
    ("A23", "请为现有A产品安排一次专项使用培训，以完成约定讲解为结果。", ["其他"], []),
    ("A24", "A项目申请R的交付请继续办理。", [], []),
    ("A25", "请办一次A实操培训，并按本次服务约定为学员开只在现场使用的临时实验配额；只验收同一场带配额实操，无单独开通目标。", ["其他", "资源需求"], [
        ("资源需求", "资源对象与交付范围", "只在现场使用的临时实验配额", "申请"),
        ("资源需求", "请求动作与用途", "为学员开只在现场使用的临时实验配额", "申请")]),
]


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: {
        ("synthetic-classification-replay", "synthetic", "synthetic-human"): "customer"})
    return Replay(tmp_path)


@pytest.mark.parametrize("code,text,labels,fields", CASES, ids=[item[0] for item in CASES])
def test_original_single_round(case, code, text, labels, fields):
    first = msg(code, 1, text)
    updates = [fact(first, *spec) for spec in fields]
    kwargs = {}
    if "其他" in labels:
        value = {"A11": "核验现有B服务适不适用，给我们书面结论", "A23": "专项使用培训",
                 "A25": "只验收同一场带配额实操，无单独开通目标"}[code]
        kwargs["other_reason"] = dict(value=value, quote=text, evidence_ids=[first.key])
    result = case.send(first, record(first, labels, updates, **kwargs))
    assert len(result.created) == 1 and not result.updated and not result.review
    event = case.assert_current(labels, expected=[spec[:3] for spec in fields])
    assert event.status == "未知" and event.recovery is None and event.closed_at is None
    assert event.attributes == {spec[1]: spec[2] for spec in fields}
    assert all(value["business_time"] is None for state in event.field_states.values() for value in state["facts"])
    assert event.updated_by[0]["snapshot"]["types"] == labels
    assert event.evidence[0]["digest"] == first.digest
    if not labels:
        assert event.classification_status == "待分类" and event.other_reason is None
    if code == "A08":
        follow = msg("A08-quantity", 2, "本次A环境申请提到2000次/分钟。")
        update = fact(follow, "资源需求", "目标总量与增减量", "提到2000次/分钟", "目标总量")
        case.send(follow, record(follow, labels, [update], old=event, anchor=first))
        case.assert_current(labels, expected=[("资源需求", "目标总量与增减量", "提到2000次/分钟")])
        assert "目标总量与增减量" not in case.old(case.event(), 1)["attributes"]
    if code == "A24":
        follow = msg("A24-clarification", 2, "申请R是按原权益提高A环境配额，没有采购。")
        case.send(follow, record(follow, ["资源需求"], [fact(follow, "资源需求", "请求动作与用途", "提高A环境配额", "申请")], old=event, anchor=first))
        assert case.event().event_id == event.event_id
        case.assert_current(["资源需求"])
        assert case.old(case.event(), 1)["classification_status"] == "待分类"


@pytest.mark.parametrize("initial", ["empty", "open", "closed"])
def test_A01_acknowledgement(case, initial):
    before = None
    if initial != "empty":
        first = msg("A01-initial", 1, "A下载失败，请排查。" if initial == "open" else "A下载按约定验证完成，虚构已恢复。")
        updates = [] if initial == "open" else [change(first, common("status"), "虚构已恢复", "完成")]
        case.send(first, record(first, ["排障"], updates))
        before = deepcopy(case.event())
    ack = msg("A01", 2, "早上好，收到，谢谢。")
    result = case.send(ack)
    assert not result.created and not result.updated and not result.review
    assert len(case.store.events) == (0 if initial == "empty" else 1)
    if before:
        assert case.event() == before


def test_A02_quote_request_and_answer(case):
    first = msg("A02", 1, "A服务按月怎么计费？请给现行报价。")
    case.send(first, record(first, ["商务需求"]))
    event = case.assert_current(["商务需求"])
    assert event.attributes == {}
    reminder = msg("A02-reminder", 2, "请答复这次A服务询价。")
    case.send(reminder, record(reminder, ["商务需求"], old=event, anchor=first))
    assert case.event().event_id == event.event_id
    answer = msg("A02-answer", 3, "A服务现行月价100元人民币，本次报价答复已完成。")
    updates = [fact(answer, "商务需求", "价格与金额口径", "月价100元人民币", "正式报价"),
        change(answer, common("status"), "答复已完成", "报价答复")]
    case.send(answer, record(answer, ["商务需求"], updates, old=event, anchor=first))
    case.assert_current(["商务需求"], expected=[("商务需求", "价格与金额口径", "月价100元人民币")])
    assert case.event().status == "答复已完成"
    assert "价格与金额口径" not in case.old(case.event(), 1)["attributes"]


def test_A05_purchase_evaluation_is_not_purchase(case):
    first = msg("A05", 1, "准备购买B服务，先开试用配额验证采购方案。")
    fields = [fact(first, "商机", "采购意向与评估事实", "准备购买B服务", "购买评估"),
        fact(first, "资源需求", "请求动作与用途", "开试用配额验证采购方案", "试用申请")]
    case.send(first, record(first, ["商机", "资源需求"], fields))
    event = case.assert_current(["商机", "资源需求"], absent=[("商机", "成交事实及范围")])
    verified = msg("A05-verified", 2, "本次B服务试用验证通过。")
    case.send(verified, record(verified, ["商机", "资源需求"], [fact(verified, "资源需求", "审批、交付与可用验证", "试用验证通过", "试用验证")], old=event, anchor=first))
    case.assert_current(["商机", "资源需求"], absent=[("商机", "成交事实及范围")])


@pytest.mark.parametrize("existing", [False, True])
def test_A06_order_result_reuses_identity(case, existing):
    anchor = msg("A06-intent", 1, "订单P准备采购B服务，请报价。")
    event = None
    if existing:
        case.send(anchor, record(anchor, ["商机", "商务需求"]))
        event = case.event()
    first = msg("A06", 2, "订单P已确认购买B服务，款已由我们支付。")
    fields = [fact(first, "商机", "成交事实及范围", "已确认购买B服务", "购买确认"),
        fact(first, "商务需求", "办理事实与结果", "款已由我们支付", "客户付款陈述")]
    result = case.send(first, record(first, ["商机", "商务需求"], fields, old=event, anchor=anchor if existing else None))
    case.assert_current(["商机", "商务需求"], expected=[("商务需求", "办理事实与结果", "款已由我们支付")])
    assert not result.review
    assert (result.updated == [event.event_id] and not result.created) if existing else len(result.created) == 1
    assert "价格与金额口径" not in case.event().attributes
    assert all(value["business_time"] is None for state in case.event().field_states.values() for value in state["facts"])


@pytest.mark.parametrize("code", ["A07", "A18", "A19"])
def test_existing_fault_business_boundary(case, code):
    first = msg(code + "-fault", 1, "A导出失败，请排查。" if code == "A18" else "A下载故障未恢复，请排查。")
    case.send(first, record(first, ["排障"]))
    event = case.event()
    text = {"A07": "我把后端节点扩了，继续观察。", "A18": "A导出今天又失败了，你们服务太差。",
            "A19": "本次再不恢复，我们就把A业务迁走。"}[code]
    follow = msg(code, 2, text)
    label, name, value, stage = {
        "A07": ("排障", "处置与验证结果", "把后端节点扩了", "内部处置"),
        "A18": ("排障", "症状与对照表现", "今天又失败了", "再次失败确认"),
        "A19": ("流失", "风险、计划与实际变化", "再不恢复，我们就把A业务迁走", "条件风险")}[code]
    labels = ["排障", "流失"] if code == "A19" else ["排障"]
    result = case.send(follow, record(follow, labels, [fact(follow, label, name, value, stage)], old=event, anchor=first))
    assert result.updated == [event.event_id] and not result.created
    case.assert_current(labels, expected=[(label, name, value)], absent=[("排障", "排查发现与根因"), ("排障", "恢复事实")])
    assert case.event().end_time == follow.sent_at


def test_A09_total_and_increment_correction(case):
    first = msg("A09", 1, "A现有1000次/分钟，请提到2000。")
    initial = [fact(first, "资源需求", "现有可用量", "1000次/分钟", "申请基准"),
        fact(first, "资源需求", "目标总量与增减量", "提到2000", "本次请求")]
    case.send(first, record(first, ["资源需求"], initial))
    event = case.event()
    before = deepcopy(event)
    quantity = attribute("资源需求", "目标总量与增减量")
    previous = case.adopted(event, quantity)[0]["id"]
    corrected = msg("A09-correction", 2, "不是提到2000，是再加2000。")
    update = fact(corrected, "资源需求", quantity["name"], "再加2000", "本次请求", intent="correction", replaces=[previous])
    changes = [dict(kind="correction", text="再加2000", replaces="提到2000", evidence=[ref(corrected)])]
    result = case.send(corrected, record(corrected, ["资源需求"], [update], old=event, anchor=first, changes=changes))
    assert result.updated == [event.event_id] and not result.created
    current = case.assert_current(["资源需求"], expected=[("资源需求", quantity["name"], "再加2000"), ("资源需求", "现有可用量", "1000次/分钟")])
    assert "提到2000" not in current.summary and "再加2000" in current.summary
    assert current.updated_by[0] == before.updated_by[0]
    assert case.old(current, 1)["typed_attributes"]["资源需求"][quantity["name"]]["value"] == "提到2000"
    assert "3000" not in str(current.attributes)


@pytest.mark.parametrize("existing_risk", [False, True])
def test_A20_partial_actual_loss(case, existing_risk):
    anchor = msg("A20-risk", 1, "本次A故障再不恢复，我们就把A业务迁走。")
    event = None
    labels = ["流失"]
    if existing_risk:
        case.send(anchor, record(anchor, ["排障", "流失"], [fact(anchor, "流失", "风险、计划与实际变化", "再不恢复，我们就把A业务迁走", "条件风险")]))
        event, labels = case.event(), ["排障", "流失"]
    actual = msg("A20", 16, "A业务昨天已迁走30%，余下继续留在这里。")
    update = fact(actual, "流失", "减少或转移规模", "A业务昨天已迁走30%", "实际迁移")
    update.update(business_time="昨天", time_kind="actual")
    result = case.send(actual, record(actual, labels, [update], old=event, anchor=anchor if event else None))
    assert not result.review
    current = case.assert_current(labels, expected=[("流失", "减少或转移规模", "A业务昨天已迁走30%")])
    value = case.adopted(current, attribute("流失", "减少或转移规模"))[0]
    assert value["business_time"] == "昨天" and value["time_kind"] == "actual"
    assert actual.sent_at.date().isoformat() == "2038-06-16"
    assert "去向与执行时间" not in current.attributes
    if event:
        assert result.updated == [event.event_id] and not result.created


def test_A10_learning_is_not_a_case(case):
    result = case.send(msg("A10", 1, "什么叫并发？只是学习概念，暂时没有业务要处理。"))
    assert result.processed == 1 and not result.created and not result.review and case.store.events == {}


def test_A26_independent_targets_in_original_message(case):
    first = msg("A26", 1, "给A环境提额；另请B控制台增加批量导出；合同C的发票也请催办。")
    specs = [("资源需求", "给A环境提额", "请求动作与用途", "提额"),
        ("功能需求", "另请B控制台增加批量导出", "期望行为与改善目标", "增加批量导出"),
        ("商务需求", "合同C的发票也请催办", "账单与发票要求", "发票也请催办")]
    candidates = [record(first, [label], [fact(first, label, name, value, "请求")], quote=quote, topic="A26-" + label)
                  for label, quote, name, value in specs]
    result = case.send(first, candidates)
    assert len(result.created) == 3 and len(case.store.events) == 3
    for label, quote, name, value in specs:
        event = next(e for e in case.store.events.values() if e.types == [label])
        assert event.summary == quote and event.attributes == {name: value}
        assert event.type_evidence[label][0]["quote"] == quote


def test_A27_misstated_purchase_is_retracted(case):
    first = msg("A27", 1, "付费增购A配额并报价")
    labels = ["商机", "资源需求", "商务需求"]
    fields = [fact(first, "商机", "采购意向与评估事实", "付费增购A配额", "采购意向"),
        fact(first, "商务需求", "办理对象与事项范围", "报价", "报价请求"),
        fact(first, "资源需求", "请求动作与用途", "增购A配额", "申请")]
    case.send(first, record(first, labels, fields))
    event = case.event()
    follow = msg("A27-correction", 2, "刚才采购和报价说错，本次只申请原权益内提额，没有采购报价请求。")
    resource_id = case.adopted(event, attribute("资源需求", "请求动作与用途"))[0]["id"]
    update = fact(follow, "资源需求", "请求动作与用途", "申请原权益内提额", "申请", intent="correction", replaces=[resource_id])
    result = case.send(follow, record(follow, ["资源需求"], [update], old=event, anchor=first,
        removed=["商机", "商务需求"], changes=[
            dict(kind="correction", text="申请原权益内提额", replaces="付费增购A配额", evidence=[ref(follow)]),
            dict(kind="correction", text="，没有采购报价请求", replaces="并报价", evidence=[ref(follow)])]))
    assert result.updated == [event.event_id]
    current = case.assert_current(["资源需求"], absent=[("商机", "采购意向与评估事实"), ("商务需求", "办理对象与事项范围")])
    assert current.summary == "申请原权益内提额，没有采购报价请求" and set(current.removed_types) == {"商机", "商务需求"}
    assert case.old(current, 1)["types"] == labels


def test_A28_long_gap_cross_room_continuation(case):
    first = msg("A28-first", 1, "工单T：A下载故障，请排查。", room="群甲")
    case.send(first, record(first, ["排障"]))
    event = case.event()
    second = replace(msg("A28", 10, "工单T那次A下载一直没好，继续排查。", room="群乙"), sender_id="synthetic-other-human")
    result = case.send(second, record(second, ["排障"], old=event, anchor=first, topic="下载工单换称"))
    assert result.updated == [event.event_id] and not result.created
    current = case.assert_current(["排障"])
    assert current.closed_at is None and current.start_time == first.sent_at and current.end_time == second.sent_at
    assert {proof["conversation_id"] for facts in current.type_evidence.values() for value in facts for proof in value["evidence_refs"]} == {"群甲", "群乙"}


def boundary(kind, message, parent=None):
    return dict(kind=kind, previous_event_id=parent, evidence=[ref(message)])


def recover(case, *, completed=False):
    first = msg("fault", 1, "A上传失败，请排查。")
    case.send(first, record(first, ["排障"]))
    event = case.event()
    fixed = msg("fixed", 2, "A上传虚构已恢复" if completed else "A上传已恢复")
    item = record(fixed, ["排障"], [fact(fixed, "排障", "恢复事实", "已恢复", "客户验证")], old=event, anchor=first)
    item["continuity"] = boundary("confirm_recovery", fixed)
    if completed:
        item["field_updates"].append(change(fixed, common("status"), "虚构已恢复", "跟进状态"))
    case.send(fixed, item)
    assert case.event().recovery["evidence"][0]["digest"] == fixed.digest
    return first, fixed, case.event()


def test_A29_recurrence_preserves_previous_round(case):
    first, fixed, parent = recover(case)
    before = deepcopy(parent)
    again = msg("A29", 3, "恢复后今天A上传重新失败，是新的一次。")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = boundary("recurrence", again, parent.event_id)
    result = case.send(again, item)
    assert len(result.created) == 1 and not result.updated
    assert case.event(parent.event_id) == before
    current = case.assert_current(["排障"], count=2, event_id=result.created[0])
    assert current.recurrence_of["event_id"] == parent.event_id and current.recovery is None
    assert [proof["message_id"] for proof in current.evidence] == [again.message_id]


def test_A30_recovery_correction_reuses_original_round(case):
    first, fixed, parent = recover(case)
    corrected = msg("A30", 3, "上次测试用错环境，实际上这次A一直没有恢复。")
    target = attribute("排障", "恢复事实")
    update = fact(corrected, "排障", target["name"], "一直没有恢复", "客户验证", intent="correction", replaces=[case.adopted(parent, target)[0]["id"]])
    item = record(corrected, ["排障"], [update], old=parent, anchor=first,
        changes=[dict(kind="correction", text=corrected.content, replaces=fixed.content, evidence=[ref(corrected)])])
    item["continuity"] = boundary("retract_recovery", corrected)
    result = case.send(corrected, item)
    assert result.updated == [parent.event_id] and not result.created
    current = case.assert_current(["排障"], expected=[("排障", "恢复事实", "一直没有恢复")])
    assert current.recovery is None and current.closed_at is None
    assert fixed.content not in current.summary
    assert case.old(current, 2)["recovery"] is not None


def test_A30_missplit_identity_gap_remains_visible(case):
    first, fixed, parent = recover(case)
    again = msg("A30-missplit", 3, "A上传恢复后新一次失败。")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = boundary("recurrence", again, parent.event_id)
    result = case.send(again, item)
    child = case.event(result.created[0])
    snapshot = deepcopy(child.updated_by)
    corrected = msg("A30-missplit-correction", 4, "上次测错环境，A上传一直没有恢复，这两条其实是同一次故障。")
    item = record(corrected, ["排障"], old=parent, anchor=first,
        changes=[dict(kind="correction", text=corrected.content, replaces=fixed.content, evidence=[ref(corrected)])])
    item["continuity"] = boundary("retract_recovery", corrected)
    case.send(corrected, item)
    assert case.event(parent.event_id).recovery is None
    assert case.event(child.event_id).recurrence_of["review_status"] == "pending"
    assert case.event(child.event_id).updated_by[:len(snapshot)] == snapshot
    assert len(case.store.events) == 2


def test_A33_retrospective_log_and_conflicting_completion_dates(case):
    first = msg("A33-first", 12, "A故障11月12日已按约定验证完成。")
    fields = [fact(first, "排障", "处置与验证结果", "11月12日已按约定验证完成", "约定验证")]
    fields[0].update(business_time="11月12日", time_kind="actual")
    case.send(first, record(first, ["排障"], fields))
    parent = case.event()
    log = msg("A33-log", 16, "这是11日恢复前的失败日志。")
    item = record(log, ["排障"], old=parent, anchor=first)
    item["continuity"] = boundary("historical", log)
    case.send(log, item)
    assert case.event().attributes == parent.attributes and case.event().summary == parent.summary
    later = msg("A33-collision", 16, "12日实际修复，13日完成验证。", hour=10)
    update = fact(later, "排障", "处置与验证结果", "13日完成验证", "约定验证", intent="conflict")
    update.update(business_time="13日", time_kind="actual")
    case.send(later, record(later, ["排障"], [update], old=case.event(), anchor=first))
    current = case.assert_current(["排障"], expected=[("排障", "处置与验证结果", "11月12日已按约定验证完成")])
    assert any(issue["status"] == "open" for issue in current.field_issues)
    assert "字段建议待复核" in current.summary and current.recurrence_of is None
    assert current.updated_by[0]["snapshot"]["typed_attributes"] == parent.typed_attributes


def test_A34_quote_payment_and_receipt_are_distinct(case):
    first = msg("A34", 1, "请按10000给A方案报价。")
    case.send(first, record(first, ["商务需求"], [fact(first, "商务需求", "价格与金额口径", "10000", "客户报价要求")]))
    parent = case.event()
    for day, text, value, stage in [(2, "客户已经付了", "客户已经付了", "付款转述"), (3, "我们仍未收到款", "仍未收到款", "到账观察")]:
        follow = msg("A34-" + str(day), day, text)
        case.send(follow, record(follow, ["商务需求"], [fact(follow, "商务需求", "办理事实与结果", value, stage)], old=case.event(), anchor=first))
        assert case.event().event_id == parent.event_id and not case.event().field_issues
    current = case.assert_current(["商务需求"], expected=[("商务需求", "价格与金额口径", "10000"), ("商务需求", "办理事实与结果", "客户已经付了；仍未收到款")])
    assert current.assignees == [] and current.closed_at is None
    assert all(value["business_time"] is None for state in current.field_states.values() for value in state["facts"])


def test_A35_read_body_and_unread_attachment(case):
    body = replace(msg("A35-body", 1, "A下载失败，详情见[图片]。"), markers=("[图片]",))
    review = record(body, [], changes=[])
    review.update(decision="review", possible_event_ids=[], decision_reason="正文可读，图片未读，图片事实待核实")
    first_result = case.send(body, (record(body, ["排障"], [fact(body, "排障", "症状与对照表现", "A下载失败", "客户报告")]), review))
    assert first_result.review and "图片未读" in first_result.review[0]["reason"]
    current = case.assert_current(["排障"], expected=[("排障", "症状与对照表现", "A下载失败")], absent=[("排障", "排查发现与根因")])
    attachment = replace(msg("A35-file", 2, "[文件]"), markers=("[文件]",))
    item = record(attachment, [], changes=[])
    item.update(decision="review", possible_event_ids=[], decision_reason="附件未读，无正文可辨认对象和目标")
    result = case.send(attachment, item)
    assert len(case.store.events) == 1 and not result.created and not result.updated
    assert result.review[0]["message_ids"] == [attachment.message_id]
    assert result.review[0]["possible_event_ids"] == []
    assert case.event() == current and attachment.message_id not in {proof["message_id"] for proof in current.evidence}


@pytest.mark.parametrize("code", ["A31", "A32"])
def test_independent_related_goal(case, code):
    first = msg(code + "-first", 1, "工单T：A故障排查修复，按原条件继续验证。" if code == "A31" else "工单T：A下载异常，请排查。", room="群甲")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    continuation = msg(code + "-continue", 2, "A修复已上线，按原条件继续验证。" if code == "A31" else "继续T的同次下载排查", room="群乙")
    case.send(continuation, record(continuation, ["排障"], old=parent, anchor=first))
    assert len(case.store.events) == 1 and case.event().closed_at is None
    text = ("基于本次方案，另为A的新客户端B做独立兼容性评估，单独出结论，原工单仍未结案。" if code == "A31"
            else "因这次故障，我们另立迁移项目M，决定迁出A。")
    new = msg(code, 3, text, room="群乙")
    labels = ["其他"] if code == "A31" else ["流失"]
    kwargs = dict(other_reason=dict(value="独立兼容性评估，单独出结论", quote=text, evidence_ids=[new.key])) if code == "A31" else {}
    item = record(new, labels, **kwargs)
    item["relations"] = [dict(kind="based_on" if code == "A31" else "caused_by", event_id=parent.event_id,
        intent="attach", reason=text, evidence=[ref(new)])]
    item["basis"].append(ref(first))
    result = case.send(new, item)
    assert len(result.created) == 1 and not result.updated
    current = case.assert_current(labels, count=2, event_id=result.created[0])
    assert current.relations[0]["event_id"] == parent.event_id
    assert current.relations[0]["review_status"] == "confirmed"
    assert case.event(parent.event_id).types == ["排障"]
