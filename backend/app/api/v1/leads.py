"""
Leads and Opportunities Endpoints.
Supports listing with filters, pagination, lead detail drawer view, and atomic claiming.
"""

import json
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel

from backend.app.core.db import transaction
from backend.app.core.security import get_current_user

router = APIRouter(prefix="/leads", tags=["Leads & Pipeline"])

ADMIN_ASSIGN_ROLES = ["admin", "campaign_manager"]


class ClaimLeadRequest(BaseModel):
    user_id: Optional[str] = None


class LeadSummaryDTO(BaseModel):
    id: str
    organization_name: str
    title: str
    opportunity_type: str
    source_name: str
    source_url: str
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    salary_text: Optional[str] = None
    location: Optional[str] = None
    status: str
    score: int
    score_band: str
    assigned_to: Optional[str] = None
    discovered_at: str


class PaginatedLeadsResponse(BaseModel):
    items: List[LeadSummaryDTO]
    total: int
    page: int
    page_size: int
    total_pages: int


# In-memory baseline records for offline/initial state testing
SAMPLE_LEADS = [
    {
        "id": "b0000001-0000-0000-0000-000000000001",
        "organization_name": "Acme AI Systems",
        "title": "Junior Python Developer (0-1 yrs)",
        "opportunity_type": "job",
        "source_name": "Internshala",
        "source_url": "https://internshala.com/job/detail/junior-python-001",
        "experience_min": 0,
        "experience_max": 1,
        "salary_text": "₹6,00,000 - ₹8,00,000 PA",
        "location": "Bengaluru, India",
        "status": "verified",
        "score": 92,
        "score_band": "high",
        "assigned_to": None,
        "discovered_at": "2026-10-16T08:00:00Z"
    },
    {
        "id": "b0000002-0000-0000-0000-000000000002",
        "organization_name": "Beta Cloud Labs",
        "title": "Cloud Backend Intern",
        "opportunity_type": "internship",
        "source_name": "Internshala",
        "source_url": "https://internshala.com/internship/detail/cloud-intern-002",
        "experience_min": 0,
        "experience_max": 0,
        "salary_text": "₹30,000 /month",
        "location": "Remote",
        "status": "draft_ready",
        "score": 88,
        "score_band": "high",
        "assigned_to": None,
        "discovered_at": "2026-10-16T09:15:00Z"
    },
    {
        "id": "b0000003-0000-0000-0000-000000000003",
        "organization_name": "Gamma Data Fintech",
        "title": "Associate Data Analyst (0-2 yrs)",
        "opportunity_type": "job",
        "source_name": "Naukri Freshers",
        "source_url": "https://naukri.com/job/detail/data-analyst-003",
        "experience_min": 0,
        "experience_max": 2,
        "salary_text": "Competitive",
        "location": "Mumbai, India",
        "status": "needs_enrichment",
        "score": 68,
        "score_band": "medium",
        "assigned_to": None,
        "discovered_at": "2026-10-15T14:20:00Z"
    }
]


@router.get("", response_model=PaginatedLeadsResponse)
def list_leads(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    source: Optional[str] = None,
    score_band: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Lists leads with filtering, score bands, and server-side pagination."""
    items = SAMPLE_LEADS.copy()

    if source:
        items = [i for i in items if source.lower() in i["source_name"].lower()]
    if score_band:
        items = [i for i in items if i["score_band"] == score_band.lower()]
    if status:
        items = [i for i in items if i["status"] == status.lower()]
    if search:
        s = search.lower()
        items = [i for i in items if s in i["title"].lower() or s in i["organization_name"].lower()]

    total = len(items)
    start = (page - 1) * page_size
    paged_items = items[start:start + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedLeadsResponse(
        items=paged_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get("/{id}")
def get_lead_by_id(id: str, current_user: dict = Depends(get_current_user)):
    """Retrieves full lead context, score breakdown, and contact history."""
    for lead in SAMPLE_LEADS:
        if lead["id"] == id:
            detailed = lead.copy()
            detailed["description"] = "Full job description for this verified opportunity."
            detailed["contacts"] = [
                {
                    "id": "c0000001-0000-0000-0000-000000000001",
                    "name": "Sarah Jenkins",
                    "role_title": "Head of Talent Acquisition",
                    "methods": [
                        {
                            "id": "m0000001-0000-0000-0000-000000000001",
                            "kind": "email",
                            "value": "sjenkins@example.com",
                            "verified_status": "deliverable",
                            "verified_at": "2026-10-16T10:00:00Z"
                        }
                    ]
                }
            ]
            detailed["score_reasons"] = [
                {"code": "OFFICIAL_COMPANY_SOURCE", "description": "Verified official employer source", "points": 25},
                {"code": "CONTACT_ROLE_CONFIRMED", "description": "Recruiter role identified", "points": 25},
                {"code": "EMAIL_VERIFIED_DELIVERABLE", "description": "Deliverable email verified", "points": 20}
            ]
            return detailed

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Lead with id '{id}' not found")


@router.post("/{id}/claim")
def claim_lead(
    id: str,
    payload: ClaimLeadRequest = ClaimLeadRequest(),
    current_user: dict = Depends(get_current_user)
):
    
    target_user_id = payload.user_id or current_user["id"]

    if payload.user_id and payload.user_id != current_user["id"]:
        if current_user.get("role") not in ADMIN_ASSIGN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins and campaign managers may assign leads to another user"
            )

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE opportunities
                SET assigned_to = %s, claimed_at = NOW()
                WHERE id = %s AND assigned_to IS NULL
                RETURNING id, assigned_to, claimed_at, status;
                """,
                (target_user_id, id)
            )
            updated = cur.fetchone()

            if updated is None:
                cur.execute("SELECT assigned_to FROM opportunities WHERE id = %s;", (id,))
                existing = cur.fetchone()
                if existing is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Lead with id '{id}' not found"
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Lead has already been claimed by another team member"
                )

            columns = [desc[0] for desc in cur.description]
            lead_row = dict(zip(columns, updated))

            cur.execute(
                """
                INSERT INTO audit_logs (actor, action, entity_type, entity_id, details)
                VALUES (%s, %s, %s, %s, %s);
                """,
                (
                    current_user["id"],
                    "lead_claimed",
                    "opportunity",
                    id,
                    json.dumps({
                        "claimed_by": target_user_id,
                        "assigned_by": current_user["id"],
                        "assigned_by_role": current_user.get("role")
                    })
                )
            )

    return {
        "id": str(lead_row["id"]),
        "assigned_to": str(lead_row["assigned_to"]),
        "claimed_at": lead_row["claimed_at"].isoformat(),
        "status": lead_row["status"]
    }
