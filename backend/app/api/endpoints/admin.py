"""
Admin API endpoints for managing approvals, document verification, and manual reviews.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from collections import defaultdict
import statistics

from ...models.document import DocumentModel, DocumentStatus, VerificationMethod
from ...models.workflow import WorkflowInstance, StepExecution, WorkflowDefinition, AssignmentStatus
from ...auth.provider import get_current_user, require_permission
from ...schemas.admin import (
    PendingApprovalResponse,
    PendingDocumentResponse,
    PendingSignatureResponse,
    ManualReviewResponse,
    AdminStatsResponse,
    DashboardResponse,
    SystemMetrics,
    PendingItemsBreakdown,
    WorkflowMetrics,
    PerformanceMetrics,
    TimeSeriesMetric
)

router = APIRouter()

# Admin dependency
async def get_current_admin(current_user: dict = Depends(require_permission("VIEW_DOCUMENTS"))):
    return current_user

@router.get("/pending-approvals", response_model=List[PendingApprovalResponse])
async def get_pending_approvals(
    admin: dict = Depends(get_current_admin),
    assigned_to: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all pending workflow approvals requiring admin action."""
    try:
        # Mock data for now - in production, this would query WorkflowInstance collection
        mock_approvals = [
            PendingApprovalResponse(
                instance_id="inst_001",
                workflow_name="Citizen Registration",
                citizen_name="Maria González",
                citizen_id="citizen_001",
                step_name="Age Verification",
                submitted_at=datetime.utcnow() - timedelta(hours=2),
                priority="high",
                approval_type="age_verification",
                context={"age": 17, "requires_guardian": True},
                assigned_to=str(admin.id)
            ),
            PendingApprovalResponse(
                instance_id="inst_002",
                workflow_name="Building Permit",
                citizen_name="John Smith",
                citizen_id="citizen_002",
                step_name="Manual Review",
                submitted_at=datetime.utcnow() - timedelta(hours=4),
                priority="medium",
                approval_type="permit_approval",
                context={"permit_type": "residential", "area": "250 sqm"},
                assigned_to="admin_002"
            )
        ]
        
        return mock_approvals[offset:offset+limit]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending approvals: {str(e)}")

@router.get("/documents", response_model=List[PendingDocumentResponse])
async def get_all_documents(
    admin: dict = Depends(get_current_admin),
    status: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    citizen_name: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    verification_priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents (pending, verified, rejected) with filters."""
    try:
        query = {}
        
        # Apply filters
        if status:
            if status == "verified":
                query["status"] = DocumentStatus.VERIFIED
            elif status == "rejected":
                query["status"] = DocumentStatus.REJECTED
            elif status == "pending_verification":
                query["status"] = DocumentStatus.PENDING
            else:
                query["status"] = status
                
        if document_type:
            query["document_type"] = document_type
        if verification_priority:
            query["verification_priority"] = verification_priority
        
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        filtered_docs = []
        for doc in documents:
            # Mock citizen name
            citizen_name_value = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Filter by citizen name if provided
            if citizen_name and citizen_name.lower() not in citizen_name_value.lower():
                continue
                
            filtered_docs.append(PendingDocumentResponse(
                document_id=doc.document_id,
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name_value,
                citizen_id=str(doc.citizen_id),
                uploaded_at=doc.created_at,
                file_size=getattr(doc.metadata, "file_size", 1024000),  # Default 1MB
                mime_type=getattr(doc.metadata, "mime_type", "application/pdf"),  # Default PDF
                status=doc.status,  # Include actual document status
                verification_priority=getattr(doc.metadata, "verification_priority", "normal"),
                previous_attempts=getattr(doc.metadata, "verification_attempts", 0)
            ))
        
        return filtered_docs
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch documents: {str(e)}")

@router.get("/pending-documents", response_model=List[PendingDocumentResponse])
async def get_pending_documents(
    admin: dict = Depends(get_current_admin),
    document_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents pending verification."""
    try:
        # Query actual documents in the database
        query = {"status": DocumentStatus.PENDING}
        
        if document_type:
            query["document_type"] = document_type
            
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        pending_documents = []
        for doc in documents:
            # Mock citizen info for now
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Determine priority based on document type
            priority = "normal"
            if doc.document_type in ["national_id", "passport"]:
                priority = "urgent"
            elif getattr(doc.metadata, "verification_attempts", 0) > 1:
                priority = "low"
            
            pending_documents.append(PendingDocumentResponse(
                document_id=str(doc.document_id),
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                uploaded_at=doc.created_at,
                file_size=getattr(doc.metadata, "file_size", 0),
                mime_type=getattr(doc.metadata, "mime_type", "application/pdf"),
                status=doc.status,  # Include actual document status
                verification_priority=priority,
                previous_attempts=getattr(doc.metadata, "verification_attempts", 0)
            ))
        
        return pending_documents
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending documents: {str(e)}")

@router.get("/pending-signatures", response_model=List[PendingSignatureResponse])
async def get_pending_signatures(
    admin: dict = Depends(get_current_admin),
    assigned_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all documents requiring administrative signatures."""
    try:
        # Find documents that need signatures (permits, certificates, etc.)
        query = {
            "document_type": {"$in": ["permit", "certificate", "approval_letter"]},
            "status": DocumentStatus.VERIFIED,
            "signatures": {"$size": 0}  # No signatures yet
        }
        
        documents = await DocumentModel.find(query).skip(offset).limit(limit).to_list()
        
        pending_signatures = []
        for doc in documents:
            # Mock citizen info
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            # Determine signature type and deadline
            signature_type = doc.document_type
            deadline = doc.created_at + timedelta(days=3)  # 3-day SLA
            
            pending_signatures.append(PendingSignatureResponse(
                document_id=str(doc.document_id),
                title=doc.title,
                document_type=doc.document_type,
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                workflow_name=getattr(doc.metadata, "workflow_name", "Unknown"),
                signature_type=signature_type,
                requires_signature_at=doc.verified_at or doc.created_at,
                deadline=deadline
            ))
        
        return pending_signatures
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch pending signatures: {str(e)}")

@router.get("/manual-reviews", response_model=List[ManualReviewResponse])
async def get_manual_reviews(
    admin: dict = Depends(get_current_admin),
    review_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """Get all items requiring manual review (duplicates, fraud, anomalies)."""
    try:
        # Check for documents with low confidence scores (potential fraud)
        low_confidence_docs = await DocumentModel.find({
            "metadata.confidence_score": {"$lt": 0.7},
            "status": DocumentStatus.PENDING
        }).limit(limit).to_list()
        
        manual_reviews = []
        for doc in low_confidence_docs:
            citizen_name = f"Citizen {str(doc.citizen_id)[-3:]}"
            
            manual_reviews.append(ManualReviewResponse(
                review_id=f"fraud_{doc.document_id}",
                type="fraud_check",
                citizen_name=citizen_name,
                citizen_id=str(doc.citizen_id),
                workflow_name=getattr(doc.metadata, "workflow_name", "Document Verification"),
                issue_description=f"Document authenticity flagged by automated verification. Confidence: {getattr(doc.metadata, 'confidence_score', 0):.1%}",
                severity="critical",
                created_at=doc.created_at,
                context={
                    "document_id": str(doc.document_id),
                    "document_type": doc.document_type,
                    "confidence_score": getattr(doc.metadata, "confidence_score", 0),
                    "fraud_indicators": getattr(doc.metadata, "fraud_indicators", [])
                }
            ))
        
        # Add some mock duplicate detection reviews
        if len(manual_reviews) == 0:
            manual_reviews.append(ManualReviewResponse(
                review_id="dup_001",
                type="duplicate_detection",
                citizen_name="Luis Fernando",
                citizen_id="citizen_009",
                workflow_name="Citizen Registration",
                issue_description="Potential duplicate registration detected. Same name and birth date found in system.",
                severity="high",
                created_at=datetime.utcnow() - timedelta(hours=1),
                context={
                    "duplicate_count": 2,
                    "citizen_ids": ["citizen_009", "citizen_010"],
                    "matching_fields": ["name", "date_of_birth"]
                }
            ))
        
        return manual_reviews
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch manual reviews: {str(e)}")

@router.post("/instances/{instance_id}/approve")
async def process_approval(
    instance_id: str,
    decision: str = Body(...),
    comments: str = Body(...),
    admin: dict = Depends(get_current_admin)
):
    """Process a workflow approval decision."""
    try:
        # Mock approval processing for now
        print(f"Processing approval for instance {instance_id}: {decision} - {comments}")
        
        return {"message": "Approval processed successfully", "instance_id": instance_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process approval: {str(e)}")

@router.post("/documents/{document_id}/admin-verify")
async def process_document_verification(
    document_id: str,
    decision: str = Body(...),
    comments: str = Body(...),
    admin: dict = Depends(get_current_admin)
):
    """Process document verification decision."""
    try:
        # Find document by document_id field (not MongoDB _id)
        document = await DocumentModel.find_one(DocumentModel.document_id == document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if decision == "approve":
            document.status = DocumentStatus.VERIFIED
            document.verification_method = VerificationMethod.MANUAL
            document.verified_by = str(admin.id)
            document.verified_at = datetime.utcnow()
            document.verification_notes = comments
            # Update metadata safely
            if hasattr(document.metadata, 'confidence_score'):
                document.metadata.confidence_score = 1.0
        else:
            document.status = DocumentStatus.REJECTED
            document.verification_notes = comments
            
        # Update attempts safely
        if hasattr(document.metadata, 'verification_attempts'):
            document.metadata.verification_attempts = getattr(document.metadata, "verification_attempts", 0) + 1
        
        document.updated_at = datetime.utcnow()
        await document.save()
        
        return {"message": "Document verification processed", "document_id": document_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process verification: {str(e)}")

@router.post("/manual-reviews/{review_id}/resolve")
async def resolve_manual_review(
    review_id: str,
    resolution: str = Body(...),
    resolution_notes: str = Body(...),
    priority: str = Body("normal"),
    admin: dict = Depends(get_current_admin)
):
    """Resolve a manual review item."""
    try:
        # Mock resolution for now
        print(f"Resolving review {review_id}: {resolution} - {resolution_notes}")
        
        return {"message": "Manual review resolved", "review_id": review_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resolve review: {str(e)}")

@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(admin: dict = Depends(get_current_admin)):
    """Get administrative dashboard statistics."""
    try:
        # Count pending documents (real data)
        pending_documents = await DocumentModel.find({
            "status": DocumentStatus.PENDING
        }).count()

        # Count documents needing signatures (real data)
        pending_signatures = await DocumentModel.find({
            "document_type": {"$in": ["permit", "certificate", "approval_letter"]},
            "status": DocumentStatus.VERIFIED,
            "signatures": {"$size": 0}
        }).count()

        # Mock data for workflow approvals and manual reviews
        pending_approvals = 2  # From mock data above
        manual_reviews = 1    # From mock data above

        return AdminStatsResponse(
            pending_approvals=pending_approvals,
            pending_documents=pending_documents,
            pending_signatures=pending_signatures,
            manual_reviews=manual_reviews,
            total_pending=pending_approvals + pending_documents + pending_signatures + manual_reviews
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get admin stats: {str(e)}")


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(admin: dict = Depends(get_current_admin)):
    """Get comprehensive dashboard data for admin interface."""
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())

        # Get all workflow instances for metrics
        all_instances = await WorkflowInstance.find().to_list()

        # Get unique citizens (users who have created instances)
        unique_citizens = set()
        for instance in all_instances:
            unique_citizens.add(instance.user_id)

        # Calculate instances created today and this week
        instances_today = [i for i in all_instances if i.created_at >= today_start]
        instances_week = [i for i in all_instances if i.created_at >= week_start]
        completed_today = [i for i in all_instances if i.completed_at and i.completed_at >= today_start]
        completed_week = [i for i in all_instances if i.completed_at and i.completed_at >= week_start]

        # System metrics
        system_metrics = SystemMetrics(
            total_active_citizens=len(unique_citizens),
            total_workflow_instances=len(all_instances),
            instances_created_today=len(instances_today),
            instances_completed_today=len(completed_today),
            instances_created_this_week=len(instances_week),
            instances_completed_this_week=len(completed_week)
        )

        # Get pending items breakdown
        pending_documents = await DocumentModel.find({
            "status": DocumentStatus.PENDING
        }).count()

        pending_signatures = await DocumentModel.find({
            "document_type": {"$in": ["permit", "certificate", "approval_letter"]},
            "status": DocumentStatus.VERIFIED,
            "signatures": {"$size": 0}
        }).count()

        # Count instances with pending approvals
        pending_approval_instances = [
            i for i in all_instances
            if i.assignment_status in [AssignmentStatus.PENDING_REVIEW, AssignmentStatus.UNDER_REVIEW]
        ]

        # Count manual reviews (documents with low confidence)
        manual_review_docs = await DocumentModel.find({
            "metadata.confidence_score": {"$lt": 0.7},
            "status": DocumentStatus.PENDING
        }).count()

        # Priority breakdown
        priority_breakdown = defaultdict(int)
        for instance in pending_approval_instances:
            priority = instance.priority
            if priority <= 3:
                priority_breakdown["high"] += 1
            elif priority <= 7:
                priority_breakdown["medium"] += 1
            else:
                priority_breakdown["low"] += 1

        pending_items = PendingItemsBreakdown(
            pending_approvals=len(pending_approval_instances),
            pending_documents=pending_documents,
            pending_signatures=pending_signatures,
            manual_reviews=manual_review_docs,
            total_pending=len(pending_approval_instances) + pending_documents + pending_signatures + manual_review_docs,
            pending_by_priority=dict(priority_breakdown)
        )

        # Calculate workflow-specific metrics
        workflow_metrics_dict = defaultdict(lambda: {
            "total": 0, "active": 0, "completed": 0, "failed": 0,
            "processing_times": [], "pending_approvals": 0
        })

        for instance in all_instances:
            wf_id = instance.workflow_id
            workflow_metrics_dict[wf_id]["total"] += 1

            if instance.status in ["running", "awaiting_input"]:
                workflow_metrics_dict[wf_id]["active"] += 1
            elif instance.status == "completed":
                workflow_metrics_dict[wf_id]["completed"] += 1
                if instance.duration_seconds:
                    workflow_metrics_dict[wf_id]["processing_times"].append(instance.duration_seconds / 3600)
            elif instance.status == "failed":
                workflow_metrics_dict[wf_id]["failed"] += 1

            if instance.assignment_status in [AssignmentStatus.PENDING_REVIEW, AssignmentStatus.UNDER_REVIEW]:
                workflow_metrics_dict[wf_id]["pending_approvals"] += 1

        # Get workflow definitions for names
        workflow_defs = await WorkflowDefinition.find().to_list()
        workflow_names = {wd.workflow_id: wd.name for wd in workflow_defs}

        workflow_metrics = []
        for wf_id, metrics in workflow_metrics_dict.items():
            avg_time = statistics.mean(metrics["processing_times"]) if metrics["processing_times"] else 0
            success_rate = (metrics["completed"] / metrics["total"] * 100) if metrics["total"] > 0 else 0

            workflow_metrics.append(WorkflowMetrics(
                workflow_id=wf_id,
                workflow_name=workflow_names.get(wf_id, wf_id),
                total_instances=metrics["total"],
                active_instances=metrics["active"],
                completed_instances=metrics["completed"],
                failed_instances=metrics["failed"],
                average_processing_time_hours=round(avg_time, 2),
                success_rate=round(success_rate, 2),
                pending_approvals=metrics["pending_approvals"]
            ))

        # Sort by total instances for top workflows
        workflow_metrics.sort(key=lambda x: x.total_instances, reverse=True)

        # Calculate overall performance metrics
        all_processing_times = []
        completed_count = 0
        failed_count = 0
        abandoned_count = 0

        for instance in all_instances:
            if instance.status == "completed":
                completed_count += 1
                if instance.duration_seconds:
                    all_processing_times.append(instance.duration_seconds / 3600)
            elif instance.status == "failed":
                failed_count += 1
            elif instance.status == "abandoned":
                abandoned_count += 1

        total_finished = completed_count + failed_count + abandoned_count

        performance_metrics = PerformanceMetrics(
            average_processing_time_hours=round(statistics.mean(all_processing_times), 2) if all_processing_times else 0,
            median_processing_time_hours=round(statistics.median(all_processing_times), 2) if all_processing_times else 0,
            success_rate=round((completed_count / total_finished * 100), 2) if total_finished > 0 else 0,
            failure_rate=round((failed_count / total_finished * 100), 2) if total_finished > 0 else 0,
            abandonment_rate=round((abandoned_count / total_finished * 100), 2) if total_finished > 0 else 0,
            bottleneck_steps=[]  # Would need step execution analysis
        )

        # Generate recent activity time series (last 7 days)
        recent_activity = []
        for i in range(7):
            day_start = today_start - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            day_instances = [
                inst for inst in all_instances
                if day_start <= inst.created_at < day_end
            ]
            recent_activity.append(TimeSeriesMetric(
                timestamp=day_start,
                value=len(day_instances),
                label=day_start.strftime("%a")
            ))
        recent_activity.reverse()  # Show chronologically

        # Top workflows
        top_workflows = [
            {
                "workflow_id": wm.workflow_id,
                "name": wm.workflow_name,
                "instances": wm.total_instances,
                "success_rate": wm.success_rate
            }
            for wm in workflow_metrics[:5]
        ]

        # Staff workload (based on assigned instances)
        staff_workload = defaultdict(int)
        for instance in all_instances:
            if instance.assigned_user_id:
                staff_workload[instance.assigned_user_id] += 1

        # System health check
        system_health = {
            "status": "healthy",
            "database": "connected",
            "pending_items_backlog": pending_items.total_pending > 100,
            "high_priority_items": priority_breakdown.get("high", 0),
            "average_response_time_ms": 150,  # Would need actual monitoring
            "last_check": now.isoformat()
        }

        return DashboardResponse(
            system_metrics=system_metrics,
            pending_items=pending_items,
            workflow_metrics=workflow_metrics,
            performance_metrics=performance_metrics,
            recent_activity=recent_activity,
            top_workflows=top_workflows,
            staff_workload=dict(staff_workload),
            system_health=system_health,
            last_updated=now
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard data: {str(e)}")

@router.post("/documents/{document_id}/sign")
async def sign_document(
    document_id: str,
    signature_method: str = Body(...),
    signature_data: str = Body(...),
    comments: str = Body(None),
    admin: dict = Depends(get_current_admin)
):
    """Add administrative signature to a document."""
    try:
        document = await DocumentModel.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Add signature to document metadata for now
        if "signatures" not in document.metadata:
            document.metadata["signatures"] = []
        
        document.metadata["signatures"].append({
            "signer_id": str(admin.id),
            "signer_name": admin.full_name,
            "signer_role": admin.role,
            "signature_method": signature_method,
            "signature_data": signature_data,
            "signed_at": datetime.utcnow().isoformat(),
            "comments": comments
        })
        
        document.updated_at = datetime.utcnow()
        await document.save()
        
        return {"message": "Document signed successfully", "document_id": document_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sign document: {str(e)}")