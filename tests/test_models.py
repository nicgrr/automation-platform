from automation_control.approvals import ApprovalStateError, decide_approval
from automation_control.models import (
    Approval,
    ApprovalStatus,
    EbayListing,
    JobRun,
    JobStatus,
    MarketObservation,
    PriceRecommendation,
    RecommendationStatus,
    ToolExecution,
    ToolExecutionStatus,
    User,
)


def test_approval_can_only_be_decided_once(session):
    approval = Approval(requested_by="agent", action="future.write", arguments={})
    session.add(approval)
    session.commit()
    decide_approval(session, approval, approved=True, actor="owner")
    assert approval.status is ApprovalStatus.APPROVED

    try:
        decide_approval(session, approval, approved=False, actor="owner")
    except ApprovalStateError:
        pass
    else:
        raise AssertionError("second decision was accepted")


def test_job_defaults_to_pending(session):
    job = JobRun(job_type="grocery.snapshot", arguments={})
    session.add(job)
    session.commit()
    assert job.status is JobStatus.PENDING


def test_phase_1a_schema_records_ebay_observations_and_recommendations(session):
    user = User(username="owner")
    listing = EbayListing(
        ebay_listing_id="sandbox-1",
        sku="SKU-1",
        title="Test item",
        currency="AUD",
        current_price_minor=1000,
        listing_status="active",
    )
    session.add_all([user, listing])
    session.flush()
    observation = MarketObservation(
        listing_id=listing.id,
        query="test item",
        source_item_id="market-1",
        title="Comparable item",
        currency="AUD",
        price_minor=1200,
    )
    recommendation = PriceRecommendation(
        listing_id=listing.id,
        currency="AUD",
        current_price_minor=1000,
        recommended_price_minor=1100,
        rationale="Sandbox evidence only",
    )
    execution = ToolExecution(
        correlation_id="00000000-0000-0000-0000-000000000001",
        principal="agent:pricing",
        tool_name="recommend_ebay_price",
    )
    session.add_all([observation, recommendation, execution])
    session.commit()
    assert recommendation.status is RecommendationStatus.PROPOSED
    assert execution.status is ToolExecutionStatus.REQUESTED
