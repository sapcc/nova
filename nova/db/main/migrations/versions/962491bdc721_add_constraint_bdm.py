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

"""add constraint on BDM (instance_uuid, volume_uuid, deleted) against races

Revision ID: 962491bdc721
Revises: 2903cd72dc14
Create Date: 2026-10-02 15:48:54.813898
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '962491bdc721'
down_revision = '2903cd72dc14'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('block_device_mapping', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uniq_block_device_mapping0instance_uuid0volume_id0deleted',
            ['instance_uuid', 'volume_id', 'deleted'])
