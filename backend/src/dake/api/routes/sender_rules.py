"""账号配置 HTTP 边界；验证及原子写入交给业务服务。"""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from ..dependencies import AppServices, services
from ..sender_rule_schemas import SenderRuleList, SenderRuleSave, SenderCandidateList
from ...services.sender_rules import RuleConflict

router = APIRouter(prefix="/api/v1/sender-rules", tags=["sender_rules"])
Deps = Annotated[AppServices, Depends(services)]


@router.get("", response_model=SenderRuleList, operation_id="list_sender_rules")
def list_sender_rules(deps: Deps):
    return deps.sender_rules.list()


@router.get("/candidates", response_model=SenderCandidateList, operation_id="search_sender_candidates")
def search_sender_candidates(deps: Deps, keyword: str = Query("", max_length=128),
                             offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
    return deps.sender_rules.candidates(keyword, offset, limit)


@router.post("", response_model=SenderRuleList, operation_id="save_sender_rule")
def save_sender_rule(body: SenderRuleSave, deps: Deps):
    try:
        return deps.sender_rules.save(body.rule.model_dump(), body.expected_version, body.rule_id)
    except RuleConflict as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
