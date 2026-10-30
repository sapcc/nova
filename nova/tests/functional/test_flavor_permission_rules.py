# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from nova.api import auth
from nova import test
from nova.tests import fixtures as nova_fixtures


class ProjectRuleVisibilityTest(test.TestCase):
    """Rule creation must not resolve domain-denied flavors.

    Flavor show and list enforce the domain scope, but rule creation
    resolves flavors without it. A project admin can therefore store
    an own-project ALLOW for a flavor the provider denied to the
    domain; the rule silently never applies, since domain and project
    scope are ANDed.

    How listings should present a project rule that references a
    domain-denied flavor is a separate, open question; the pinned
    choice is to keep its flavor_id visible.
    """

    def setUp(self):
        super().setUp()
        # The customer is a plain project member who may manage rules
        # for their own project only.
        root = 'os_compute_api:os-flavor-permission-rules:'
        self.policy.set_rules({
            root + 'index:project': 'role:member',
            root + 'create:project':
                'role:member and project_id:%(project_id)s',
        }, overwrite=False)
        # The provider uses the admin API, the customer the plain
        # project user; both go through the full WSGI stack.
        api = self.useFixture(nova_fixtures.OSAPIFixture(api_version='v2.1'))
        self.customer = api.api
        self.provider = api.admin_api

        # Authentication is simulated; keep the domain claim consistent.
        create_context = auth.NovaKeystoneContext._create_context

        def domain_context(*args, **kwargs):
            ctx = create_context(*args, **kwargs)
            ctx.project_domain_id = 'customer-domain'
            return ctx

        self.stub_out(
            'nova.api.auth.NovaKeystoneContext._create_context',
            staticmethod(domain_context))

        # The provider creates a public flavor and denies it to the
        # customer's domain.
        self.flavor_id = self.provider.api_post('/flavors', {'flavor': {
            'name': 'domain-hidden', 'ram': 512, 'vcpus': 1, 'disk': 1,
        }}).body['flavor']['id']
        self.rule = {'flavor_permission_rule': {
            'domain_id': 'customer-domain',
            'flavor_id': self.flavor_id,
            'effect': 'deny',
        }}
        self.provider.api_post('/flavor-permission-rules', self.rule)
        self.rule['flavor_permission_rule']['project_id'] = (
            self.customer.project_id)

        # Sanity check: the ordinary flavor endpoints hide it from the
        # customer.
        self.assertEqual(404, self.customer.api_get(
            '/flavors/' + self.flavor_id,
            check_response_status=[404]).status)
        self.assertNotIn(self.flavor_id, {
            f['id'] for f in
            self.customer.api_get('/flavors').body['flavors']})

    def test_create_rejects_domain_denied_flavor(self):
        # The customer asks for an own-project ALLOW on the denied
        # flavor. Creation must answer 404, exactly as for a
        # nonexistent flavor, instead of storing a rule that can never
        # take effect: the domain deny is ANDed above the project rule.
        #
        # Manual equivalent, using the SDK with a customer that may
        # manage only its own project rules:
        #
        #     provider.compute.create_flavor(
        #         id=flavor_id, name='domain-hidden', ram=512, vcpus=1,
        #         disk=1, is_public=True)
        #     provider.compute.request(
        #         '/flavor-permission-rules', 'POST',
        #         json={'flavor_permission_rule': {
        #             'domain_id': domain_id, 'flavor_id': flavor_id,
        #             'effect': 'deny'}})
        #     customer.compute.request(
        #         f'/flavors/{flavor_id}', 'GET')  # 404, as expected
        #     customer.compute.request(
        #         '/flavor-permission-rules', 'POST',
        #         json={'flavor_permission_rule': {
        #             'domain_id': domain_id, 'project_id': project_id,
        #             'flavor_id': flavor_id, 'effect': 'allow'}})
        #     # Currently answers 201: it confirms that the hidden
        #     # flavor exists, although the rule can never apply.
        response = self.customer.api_post(
            '/flavor-permission-rules', self.rule,
            check_response_status=[201, 404])
        self.assertEqual(404, response.status)

    def test_list_reports_flavor_of_preexisting_project_rule(self):
        # Open question, pinned: a project rule can predate or outlive
        # a domain deny for the same flavor, and the listing keeps
        # reporting its flavor_id. Hiding it would render a
        # flavor-specific rule indistinguishable from a scope default
        # (flavor_id: null), and customers must keep seeing and being
        # able to delete their own rules. Seeded by the provider, so
        # this holds regardless of the creation fix.
        self.provider.api_post('/flavor-permission-rules', self.rule)
        rules = self.customer.api_get(
            '/flavor-permission-rules').body['flavor_permission_rules']
        self.assertIn(self.flavor_id, {r['flavor_id'] for r in rules})
