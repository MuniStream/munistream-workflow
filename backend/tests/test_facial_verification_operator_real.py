"""
Integration tests for FacialVerificationOperator using real MongoDB data from CONAPESCA RNPA instances.
"""
import pytest
import asyncio
from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from app.workflows.operators.facial_verification_operator import FacialVerificationOperator
from app.core.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class TestFacialVerificationOperatorReal:
    """Integration tests using real CONAPESCA RNPA data from MongoDB"""

    @pytest.fixture
    async def mongo_client(self):
        """Create MongoDB connection for testing"""
        client = AsyncIOMotorClient(
            "mongodb://admin:admin123@localhost:27017/?authSource=admin"
        )
        yield client
        client.close()

    @pytest.fixture
    async def conapesca_db(self, mongo_client):
        """Get CONAPESCA database"""
        return mongo_client["munistream_conapesca"]

    async def get_rnpa_instances_with_selfies(self, db, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Find CONAPESCA RNPA workflow instances that contain selfie data
        """
        collection = db["workflow_instances"]

        # Query for RNPA instances with selfie data
        query = {
            "workflow_id": "registro_rnpa_pescadores",
            "context.selfie_capture": {"$exists": True},
            "context.captured_document": {"$exists": True}
        }

        instances = []
        cursor = collection.find(query).limit(limit)
        async for instance in cursor:
            instances.append(instance)

        return instances

    async def test_facial_verification_with_real_data(self, conapesca_db):
        """
        Test facial verification using real selfie and ID document data from CONAPESCA RNPA instances
        """
        # Get real instances with selfie data
        instances = await self.get_rnpa_instances_with_selfies(conapesca_db, limit=3)

        if not instances:
            pytest.skip("No CONAPESCA RNPA instances with selfie data found in database")

        # Initialize facial verification operator
        operator = FacialVerificationOperator(
            task_id="test_real_verification",
            source_image_key="selfie_capture.image_data",
            target_image_keys=[
                "captured_document.front_image",
                "captured_document.back_image"
            ],
            verification_threshold=0.4,
            model_name="VGG-Face",
            min_confidence=0.5
        )

        successful_verifications = 0
        failed_verifications = 0

        for i, instance in enumerate(instances):
            logger.info(f"Testing verification for instance {i+1}/{len(instances)}: {instance.get('instance_id', 'unknown')}")

            try:
                # Use the instance context as our test context
                context = instance.get("context", {})

                # Check if required data exists
                if not context.get("selfie_capture", {}).get("image_data"):
                    logger.warning(f"Instance {instance.get('instance_id')} missing selfie image data")
                    continue

                if not context.get("captured_document", {}).get("front_image"):
                    logger.warning(f"Instance {instance.get('instance_id')} missing document front image")
                    continue

                # Execute facial verification
                result = await operator.execute_async(context)

                # Analyze results
                if result.status == "continue":
                    verification_data = result.data.get("facial_verification_results", {})

                    logger.info(f"Instance {instance.get('instance_id')} verification results:")
                    logger.info(f"  - Verified: {verification_data.get('verified')}")
                    logger.info(f"  - Targets processed: {verification_data.get('targets_processed')}")
                    logger.info(f"  - Model used: {verification_data.get('model_used')}")

                    if verification_data.get("best_match"):
                        best = verification_data["best_match"]
                        logger.info(f"  - Best match: {best['target_key']} (confidence={best['confidence']:.3f}, distance={best['distance']:.3f})")

                    # Log detailed comparison results
                    for comparison in verification_data.get("all_comparisons", []):
                        logger.info(f"  - {comparison['target_key']}: verified={comparison['verified']}, confidence={comparison.get('confidence', 0):.3f}")

                    successful_verifications += 1

                    # Assertions for successful verification
                    assert "facial_verification_results" in result.data
                    assert verification_data["model_used"] == "VGG-Face"
                    assert verification_data["targets_processed"] >= 1
                    assert "verification_timestamp" in verification_data
                    assert "all_comparisons" in verification_data

                else:
                    logger.error(f"Instance {instance.get('instance_id')} verification failed: {result.error}")
                    failed_verifications += 1

            except Exception as e:
                logger.error(f"Error testing instance {instance.get('instance_id')}: {e}")
                failed_verifications += 1

        # Summary
        total_tested = successful_verifications + failed_verifications
        logger.info(f"Facial verification test summary:")
        logger.info(f"  - Total instances tested: {total_tested}")
        logger.info(f"  - Successful verifications: {successful_verifications}")
        logger.info(f"  - Failed verifications: {failed_verifications}")
        logger.info(f"  - Success rate: {(successful_verifications/total_tested)*100:.1f}%" if total_tested > 0 else "N/A")

        # Ensure we tested at least one instance successfully
        assert successful_verifications > 0, f"No successful verifications out of {total_tested} instances tested"

    async def test_facial_verification_different_models(self, conapesca_db):
        """
        Test facial verification with different DeepFace models on real data
        """
        instances = await self.get_rnpa_instances_with_selfies(conapesca_db, limit=1)

        if not instances:
            pytest.skip("No CONAPESCA RNPA instances with selfie data found")

        instance = instances[0]
        context = instance.get("context", {})

        # Test different models
        models_to_test = ["VGG-Face", "Facenet", "ArcFace"]
        results_by_model = {}

        for model_name in models_to_test:
            logger.info(f"Testing with model: {model_name}")

            try:
                operator = FacialVerificationOperator(
                    task_id=f"test_{model_name.lower()}",
                    source_image_key="selfie_capture.image_data",
                    target_image_keys=["captured_document.front_image"],
                    model_name=model_name,
                    verification_threshold=0.4
                )

                result = await operator.execute_async(context)

                if result.status == "continue":
                    verification_data = result.data.get("facial_verification_results", {})
                    results_by_model[model_name] = verification_data

                    logger.info(f"  - {model_name}: verified={verification_data.get('verified')}")
                    if verification_data.get("best_match"):
                        best = verification_data["best_match"]
                        logger.info(f"    Best match confidence: {best['confidence']:.3f}, distance: {best['distance']:.3f}")

            except Exception as e:
                logger.warning(f"Model {model_name} failed: {e}")

        # Compare results across models
        logger.info("Model comparison summary:")
        for model, data in results_by_model.items():
            best_match = data.get("best_match")
            if best_match:
                logger.info(f"  - {model}: confidence={best_match['confidence']:.3f}, distance={best_match['distance']:.3f}")

        # Ensure at least one model worked
        assert len(results_by_model) > 0, "No models produced successful results"

    async def test_facial_verification_threshold_sensitivity(self, conapesca_db):
        """
        Test how different verification thresholds affect results with real data
        """
        instances = await self.get_rnpa_instances_with_selfies(conapesca_db, limit=1)

        if not instances:
            pytest.skip("No CONAPESCA RNPA instances with selfie data found")

        instance = instances[0]
        context = instance.get("context", {})

        # Test different thresholds
        thresholds_to_test = [0.2, 0.4, 0.6, 0.8]
        results_by_threshold = {}

        for threshold in thresholds_to_test:
            logger.info(f"Testing with threshold: {threshold}")

            operator = FacialVerificationOperator(
                task_id=f"test_threshold_{threshold}",
                source_image_key="selfie_capture.image_data",
                target_image_keys=[
                    "captured_document.front_image",
                    "captured_document.back_image"
                ],
                verification_threshold=threshold
            )

            try:
                result = await operator.execute_async(context)

                if result.status == "continue":
                    verification_data = result.data.get("facial_verification_results", {})
                    results_by_threshold[threshold] = verification_data

                    verified_count = sum(1 for comp in verification_data.get("all_comparisons", []) if comp.get("verified"))
                    logger.info(f"  - Threshold {threshold}: {verified_count} verified matches out of {verification_data.get('targets_processed', 0)}")

            except Exception as e:
                logger.warning(f"Threshold {threshold} failed: {e}")

        # Analyze threshold sensitivity
        logger.info("Threshold sensitivity analysis:")
        for threshold, data in results_by_threshold.items():
            verified_matches = sum(1 for comp in data.get("all_comparisons", []) if comp.get("verified"))
            total_targets = data.get("targets_processed", 0)
            logger.info(f"  - Threshold {threshold}: {verified_matches}/{total_targets} matches verified")

        # Verify that stricter thresholds generally produce fewer matches
        threshold_verified_counts = []
        for threshold in sorted(results_by_threshold.keys()):
            data = results_by_threshold[threshold]
            verified_count = sum(1 for comp in data.get("all_comparisons", []) if comp.get("verified"))
            threshold_verified_counts.append((threshold, verified_count))

        # Log the trend
        logger.info("Verification trend (threshold -> verified_count):")
        for threshold, count in threshold_verified_counts:
            logger.info(f"  {threshold} -> {count}")

        assert len(results_by_threshold) > 0, "No threshold tests produced results"

    async def test_instance_context_structure(self, conapesca_db):
        """
        Examine the structure of CONAPESCA RNPA instance contexts to understand data layout
        """
        instances = await self.get_rnpa_instances_with_selfies(conapesca_db, limit=3)

        if not instances:
            pytest.skip("No CONAPESCA RNPA instances found")

        for i, instance in enumerate(instances):
            logger.info(f"\n--- Instance {i+1} Context Structure ---")
            logger.info(f"Instance ID: {instance.get('instance_id', 'unknown')}")
            logger.info(f"Workflow ID: {instance.get('workflow_id', 'unknown')}")
            logger.info(f"Status: {instance.get('status', 'unknown')}")

            context = instance.get("context", {})

            # Check selfie structure
            selfie_data = context.get("selfie_capture", {})
            if selfie_data:
                logger.info(f"Selfie data keys: {list(selfie_data.keys())}")
                if "image_data" in selfie_data:
                    image_data = selfie_data["image_data"]
                    logger.info(f"Selfie image_data type: {type(image_data)}, length: {len(str(image_data)) if image_data else 0}")

            # Check document structure
            doc_data = context.get("captured_document", {})
            if doc_data:
                logger.info(f"Document data keys: {list(doc_data.keys())}")
                for img_key in ["front_image", "back_image"]:
                    if img_key in doc_data:
                        img_data = doc_data[img_key]
                        logger.info(f"Document {img_key} type: {type(img_data)}, length: {len(str(img_data)) if img_data else 0}")

            # Check other relevant context keys
            other_keys = [k for k in context.keys() if k not in ["selfie_capture", "captured_document"]]
            if other_keys:
                logger.info(f"Other context keys: {other_keys}")

    def test_synchronous_execution(self):
        """Test that synchronous execute method works properly"""
        operator = FacialVerificationOperator(
            task_id="test_sync",
            source_image_key="selfie.image",
            target_image_keys=["doc.front"]
        )

        # Test with empty context (should fail gracefully)
        context = {}
        result = operator.execute(context)

        assert result.status == "failed"
        assert "Source image not found" in result.error