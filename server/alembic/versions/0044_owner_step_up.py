"""Persist biometric owner keys and single-use step-up challenges.

revision = 0044_owner_step_up
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_owner_step_up"
down_revision = "0043_lighter_submit_ambiguity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_owner_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("public_key_spki_b64", sa.String(length=256), nullable=False),
        sa.Column("public_key_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "enrolled_pairing_session_verifier",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name=op.f("ck_device_owner_keys_device_id_width"),
        ),
        sa.CheckConstraint(
            "algorithm = 'ECDSA_P256_SHA256'",
            name=op.f("ck_device_owner_keys_algorithm_supported"),
        ),
        sa.CheckConstraint(
            "char_length(public_key_spki_b64) BETWEEN 80 AND 256",
            name=op.f("ck_device_owner_keys_public_key_spki_bounded"),
        ),
        sa.CheckConstraint(
            "public_key_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_device_owner_keys_public_key_sha256_hex"),
        ),
        sa.CheckConstraint(
            "enrolled_pairing_session_verifier ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_device_owner_keys_pairing_verifier_hex"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_owner_keys")),
        sa.UniqueConstraint(
            "public_key_sha256",
            name="uq_device_owner_keys_public_key_sha256",
        ),
    )
    op.create_index(
        "ix_device_owner_keys_active",
        "device_owner_keys",
        ["device_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "uq_device_owner_keys_one_active",
        "device_owner_keys",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "owner_step_up_challenges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("owner_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hex", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name=op.f("ck_owner_step_up_challenges_device_id_width"),
        ),
        sa.CheckConstraint(
            "purpose ~ '^[A-Z0-9_]{1,64}$'",
            name=op.f("ck_owner_step_up_challenges_purpose_bounded"),
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_owner_step_up_challenges_payload_hash_hex"),
        ),
        sa.CheckConstraint(
            "nonce_hex ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_owner_step_up_challenges_nonce_hex_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_owner_step_up_challenges_expiry_after_issue"),
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name=op.f("ck_owner_step_up_challenges_consumption_after_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_key_id"],
            ["device_owner_keys.id"],
            name=op.f(
                "fk_owner_step_up_challenges_owner_key_id_device_owner_keys"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_owner_step_up_challenges"),
        ),
        sa.UniqueConstraint(
            "nonce_hex",
            name="uq_owner_step_up_challenges_nonce",
        ),
    )
    op.create_index(
        "ix_owner_step_up_challenges_pending",
        "owner_step_up_challenges",
        ["device_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION signalai_owner_key_monotonic() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'device_owner_keys is monotonic: DELETE forbidden';
          END IF;
          IF OLD.revoked_at IS NULL
             AND NEW.revoked_at IS NOT NULL
             AND NEW.id IS NOT DISTINCT FROM OLD.id
             AND NEW.device_id IS NOT DISTINCT FROM OLD.device_id
             AND NEW.algorithm IS NOT DISTINCT FROM OLD.algorithm
             AND NEW.public_key_spki_b64 IS NOT DISTINCT FROM OLD.public_key_spki_b64
             AND NEW.public_key_sha256 IS NOT DISTINCT FROM OLD.public_key_sha256
             AND NEW.enrolled_pairing_session_verifier IS NOT DISTINCT FROM OLD.enrolled_pairing_session_verifier
             AND NEW.enrolled_at IS NOT DISTINCT FROM OLD.enrolled_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'device_owner_keys permits only first revocation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_device_owner_keys_monotonic
        BEFORE UPDATE OR DELETE ON device_owner_keys
        FOR EACH ROW EXECUTE FUNCTION signalai_owner_key_monotonic();
        """
    )
    op.execute(
        """
        CREATE FUNCTION signalai_owner_step_up_challenge_monotonic()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'owner_step_up_challenges is monotonic: DELETE forbidden';
          END IF;
          IF OLD.consumed_at IS NULL
             AND NEW.consumed_at IS NOT NULL
             AND NEW.id IS NOT DISTINCT FROM OLD.id
             AND NEW.device_id IS NOT DISTINCT FROM OLD.device_id
             AND NEW.owner_key_id IS NOT DISTINCT FROM OLD.owner_key_id
             AND NEW.purpose IS NOT DISTINCT FROM OLD.purpose
             AND NEW.payload_hash IS NOT DISTINCT FROM OLD.payload_hash
             AND NEW.nonce_hex IS NOT DISTINCT FROM OLD.nonce_hex
             AND NEW.issued_at IS NOT DISTINCT FROM OLD.issued_at
             AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'owner_step_up_challenges permits only first consumption';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_owner_step_up_challenges_monotonic
        BEFORE UPDATE OR DELETE ON owner_step_up_challenges
        FOR EACH ROW EXECUTE FUNCTION signalai_owner_step_up_challenge_monotonic();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_owner_step_up_challenges_monotonic "
        "ON owner_step_up_challenges"
    )
    op.execute("DROP FUNCTION IF EXISTS signalai_owner_step_up_challenge_monotonic()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_device_owner_keys_monotonic ON device_owner_keys"
    )
    op.execute("DROP FUNCTION IF EXISTS signalai_owner_key_monotonic()")
    op.drop_index(
        "ix_owner_step_up_challenges_pending",
        table_name="owner_step_up_challenges",
    )
    op.drop_table("owner_step_up_challenges")
    op.drop_index("uq_device_owner_keys_one_active", table_name="device_owner_keys")
    op.drop_index("ix_device_owner_keys_active", table_name="device_owner_keys")
    op.drop_table("device_owner_keys")
