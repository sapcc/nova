# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""add_flavor_permission_rules

Revision ID: f177f53f978b
Revises: cdeec0c85668
Create Date: 2025-10-24 15:43:26.554412

NOTE: The CHECK constraint on flavors.id requires MySQL 8.0.16+ or
MariaDB 10.2.1+. Earlier versions silently accept but do not enforce it.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f177f53f978b'
down_revision = 'cdeec0c85668'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('flavors') as batch_op:
        batch_op.create_check_constraint(
            'check_flavors_id_non_negative', 'id >= 0')

    op.create_table(
        'flavor_permission_rules',
        sa.Column('created_at', sa.DateTime),
        sa.Column('updated_at', sa.DateTime),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('uuid', sa.String(36), nullable=False),
        sa.Column('domain_id', sa.String(255), nullable=False),
        sa.Column('project_id', sa.String(255), nullable=False,
                  server_default=''),
        sa.Column('flavor_id', sa.Integer, nullable=False,
                  server_default='-1'),
        sa.Column(
            'effect',
            sa.Enum('allow', 'deny', name='flavor_permission_rules0effect'),
            nullable=False),
        sa.UniqueConstraint('uuid', name='uniq_flavor_permission_rules0uuid'),
        sa.UniqueConstraint(
            'domain_id', 'project_id', 'flavor_id',
            name='uniq_flavor_permission_rules0domain_id0project_id0flavor_id'
        ),
        sa.Index('flavor_permission_rules_uuid_idx', 'uuid'),
        sa.Index('flavor_permission_rules_domain_id_project_id_flavor_id_idx',
                 'domain_id', 'project_id', 'flavor_id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8',
    )
