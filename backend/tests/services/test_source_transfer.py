"""Proof requires exact member/port/digest and preserves registered source UUIDs."""
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Batch, SourceInput
from app.services.source_identity import assign_batch_identity, register_source_input
from app.services.source_transfer import (
    register_transferred_inputs,
    transfer_forms,
    validate_transfer,
)
from app.services.upload_precheck import build_precheck_report
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize('kind', DATABASES)
def test_exact_proof_and_invalid_digest_never_change_identity(kind, tmp_path):
    samples = _algorithm_samples(tmp_path)
    archive = _write_archive(tmp_path / 'raw.zip', [('17_E6-1_X0Y0.s1p', samples['dut_s1p'])])
    mapping = tmp_path / 'mapping.csv'
    mapping.write_text('E6-1,EG0 FL0 700&5500\n')
    report = build_precheck_report(archive, tmp_path / 'prepared', {}, mapping)
    item = next(i for i in report['items'] if i['classification'] == 'dut')
    with platform_database(kind, tmp_path) as (db, batch):
        assign_batch_identity(db, batch)
        key = str(uuid4())
        register_source_input(db, batch.id, item['relpath'], 'S11', item['input_digest'],
                              item['relpath'], '17_E6-1_X0Y0.s1p', source_device_key=key)
        db.commit()
        forms = transfer_forms(db, batch, archive)
        reference, entries = validate_transfer(json.loads(forms['source_ref']),
                                               json.loads(forms['source_inputs']), report)
        assert entries[0]['source_device_key'] == key
        assert reference['source_batch_id'] == batch.source_batch_id
        wrong = deepcopy(entries)
        wrong[0]['input_digest'] = '0' * 64
        with pytest.raises(HTTPException) as exc:
            validate_transfer(reference, wrong, report)
        assert exc.value.status_code == 422
        assert db.scalar(select(SourceInput.source_device_key)) == key
        # A different database imports the explicit identity; no UUID is generated for it.
        with platform_database('sqlite', tmp_path) as (target, imported):
            imported.origin_id = reference['origin_id']
            imported.source_batch_id = reference['source_batch_id']
            register_transferred_inputs(target, imported, entries)
            target.commit()
            assert target.scalar(select(SourceInput.source_device_key)) == key
            assert target.scalar(select(Batch.source_batch_id)) == batch.source_batch_id
