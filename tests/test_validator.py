from __future__ import annotations

from focus_data_toolkit.model import FOCUS_1_4_DATASETS, dataset_columns, load_model
from focus_data_toolkit.model.validator import validate_focus_1_4


def test_model_has_the_four_datasets():
    model = load_model()
    assert set(model["datasets"]) == set(FOCUS_1_4_DATASETS)
    assert model["focus_version"] == "1.4"


def test_expected_column_counts():
    assert len(dataset_columns("Cost and Usage")) == 65
    assert len(dataset_columns("Contract Commitment")) == 30
    assert len(dataset_columns("Invoice Detail")) == 22
    assert len(dataset_columns("Billing Period")) == 6


def test_validator_flags_missing_mandatory_column():
    report = validate_focus_1_4(
        "Billing Period",
        [
            {
                "BillingPeriodStart": "2026-05-01T00:00:00Z",
                "BillingPeriodEnd": "2026-06-01T00:00:00Z",
                "BillingPeriodCreated": "2026-05-01T00:00:00Z",
                "BillingPeriodLastUpdated": "2026-06-01T00:00:00Z",
                "BillingPeriodStatus": "Closed",
                # InvoiceIssuerName intentionally missing
            }
        ],
    )
    assert not report.ok
    assert "missing_mandatory_column" in {v.rule for v in report.violations}


def test_validator_flags_empty_dataset():
    report = validate_focus_1_4("Invoice Detail", [])
    assert not report.ok
