.. -*- rst -*-

===========================================================
 Flavor permission rules (os-flavor-permission-rules)
===========================================================

Manages flavor permission rules that control which OpenStack domains and
projects can use a given public flavor. By default, only administrators can
manage flavor permission rules.

Available since microversion 2.100.

List Flavor Permission Rules
============================

.. rest_method:: GET /flavor-permission-rules

Lists flavor permission rules.

Normal response codes: 200

Error response codes: badRequest(400), unauthorized(401), forbidden(403),
itemNotFound(404)

Request
-------

.. rest_parameters:: parameters.yaml

  - domain_id: flavor_permission_rule_domain_id_query
  - effect: flavor_permission_rule_effect_query
  - flavor_id: flavor_permission_rule_flavor_id_query
  - has_flavor: flavor_permission_rule_has_flavor
  - limit: limit
  - marker: marker
  - project_id: flavor_permission_rule_project_id_query
  - scope: flavor_permission_rule_scope_query

Response
--------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rules: flavor_permission_rules_resp
  - created_at: created
  - domain_id: flavor_permission_rule_domain_id
  - effect: flavor_permission_rule_effect
  - flavor_id: flavor_permission_rule_flavor_id_resp
  - id: flavor_permission_rule_id_resp
  - links: links
  - project_id: flavor_permission_rule_project_id_resp
  - scope: flavor_permission_rule_scope_resp
  - updated_at: updated
  - flavor_permission_rules_links: flavor_permission_rules_links

**Example List Flavor Permission Rules: JSON response**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-list-resp.json
   :language: javascript

Create Flavor Permission Rule
=============================

.. rest_method:: POST /flavor-permission-rules

Creates a flavor permission rule.

Normal response codes: 201

Error response codes: badRequest(400), unauthorized(401), forbidden(403),
itemNotFound(404), conflict(409)

Request
-------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule: flavor_permission_rule
  - domain_id: flavor_permission_rule_domain_id
  - effect: flavor_permission_rule_effect
  - flavor_id: flavor_permission_rule_flavor_id_body
  - project_id: flavor_permission_rule_project_id_body

**Example Create Project-Scoped Flavor Permission Rule: JSON request**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-create-project-allow-req.json
   :language: javascript

**Example Create Domain-Scoped Flavor Permission Rule: JSON request**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-create-domain-deny-req.json
   :language: javascript

Response
--------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule: flavor_permission_rule
  - created_at: created
  - domain_id: flavor_permission_rule_domain_id
  - effect: flavor_permission_rule_effect
  - flavor_id: flavor_permission_rule_flavor_id_resp
  - id: flavor_permission_rule_id_resp
  - links: links
  - project_id: flavor_permission_rule_project_id_resp
  - scope: flavor_permission_rule_scope_resp
  - updated_at: updated

**Example Create Project-Scoped Flavor Permission Rule: JSON response**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-project-allow-resp.json
   :language: javascript

**Example Create Domain-Scoped Flavor Permission Rule: JSON response**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-domain-deny-resp.json
   :language: javascript

Show Flavor Permission Rule
===========================

.. rest_method:: GET /flavor-permission-rules/{flavor_permission_rule_id}

Shows a flavor permission rule.

Normal response codes: 200

Error response codes: unauthorized(401), itemNotFound(404)

Request
-------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule_id: flavor_permission_rule_id

Response
--------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule: flavor_permission_rule
  - created_at: created
  - domain_id: flavor_permission_rule_domain_id
  - effect: flavor_permission_rule_effect
  - flavor_id: flavor_permission_rule_flavor_id_resp
  - id: flavor_permission_rule_id_resp
  - links: links
  - project_id: flavor_permission_rule_project_id_resp
  - scope: flavor_permission_rule_scope_resp
  - updated_at: updated

**Example Show Flavor Permission Rule Details: JSON response**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-project-allow-resp.json
   :language: javascript

Update Flavor Permission Rule
=============================

.. rest_method:: PUT /flavor-permission-rules/{flavor_permission_rule_id}

Updates the effect of a flavor permission rule.

Normal response codes: 200

Error response codes: badRequest(400), unauthorized(401), itemNotFound(404)

Request
-------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule: flavor_permission_rule
  - effect: flavor_permission_rule_effect
  - flavor_permission_rule_id: flavor_permission_rule_id

**Example Update Flavor Permission Rule: JSON request**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-update-req.json
   :language: javascript

Response
--------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule: flavor_permission_rule
  - created_at: created
  - domain_id: flavor_permission_rule_domain_id
  - effect: flavor_permission_rule_effect
  - flavor_id: flavor_permission_rule_flavor_id_resp
  - id: flavor_permission_rule_id_resp
  - links: links
  - project_id: flavor_permission_rule_project_id_resp
  - scope: flavor_permission_rule_scope_resp
  - updated_at: updated

**Example Update Flavor Permission Rule: JSON response**

.. literalinclude:: ../../doc/api_samples/os-flavor-permission-rules/flavor-permission-rule-domain-deny-resp.json
   :language: javascript

Delete Flavor Permission Rule
=============================

.. rest_method:: DELETE /flavor-permission-rules/{flavor_permission_rule_id}

Deletes a flavor permission rule.

Normal response codes: 204

Error response codes: unauthorized(401), itemNotFound(404)

Request
-------

.. rest_parameters:: parameters.yaml

  - flavor_permission_rule_id: flavor_permission_rule_id

Response
--------

No body content is returned on a successful DELETE action.
