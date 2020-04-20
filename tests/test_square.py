import copy
import os
import types
import unittest.mock as mock

import square.k8s as k8s
import square.manio as manio
import square.square as square
from square.dtypes import (
    SUPPORTED_KINDS, DeltaCreate, DeltaDelete, DeltaPatch, DeploymentPlan,
    GroupBy, JsonPatch, MetaManifest, Selectors,
)
from square.k8s import urlpath

from .test_helpers import make_manifest


class TestLogging:
    def test_setup_logging(self):
        """Basic tests - mostly ensure that function runs."""

        # Test function must accept all log levels.
        for level in range(10):
            square.setup_logging(level)


class TestBasic:
    @classmethod
    def teardown_class(cls):
        pass

    def setup_method(self, method):
        # All tests must run relative to this folder because the script makes
        # assumptions about the location of the templates, tf, etc folder.
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

    def test_find_namespace_orphans(self):
        """Return all resource manifests that belong to non-existing
        namespaces.

        This function will be useful to sanity check the local deployments
        manifest to avoid cases where users define resources in a namespace but
        forget to define that namespace (or mis-spell it).

        """
        fun = square.find_namespace_orphans

        # Two deployments in the same non-existing Namespace. Both are orphaned
        # because the namespace `ns1` does not exist.
        man = {
            MetaManifest('v1', 'Deployment', 'ns1', 'foo'),
            MetaManifest('v1', 'Deployment', 'ns1', 'bar'),
        }
        assert fun(man) == (man, True)

        # Two namespaces - neither is orphaned by definition.
        man = {
            MetaManifest('v1', 'Namespace', None, 'ns1'),
            MetaManifest('v1', 'Namespace', None, 'ns2'),
        }
        assert fun(man) == (set(), True)

        # Two deployments, only one of which is inside a defined Namespace.
        man = {
            MetaManifest('v1', 'Deployment', 'ns1', 'foo'),
            MetaManifest('v1', 'Deployment', 'ns2', 'bar'),
            MetaManifest('v1', 'Namespace', None, 'ns1'),
        }
        assert fun(man) == ({MetaManifest('v1', 'Deployment', 'ns2', 'bar')}, True)

    def test_show_plan(self):
        """Just verify it runs.

        There is nothing really to tests here because the function only prints
        strings to the terminal. Therefore, we will merely ensure that all code
        paths run without error.

        """
        meta = manio.make_meta(make_manifest("Deployment", "ns", "name"))
        patch = JsonPatch(
            url="url",
            ops=[
                {'op': 'remove', 'path': '/metadata/labels/old'},
                {'op': 'add', 'path': '/metadata/labels/new', 'value': 'new'}
            ],
        )
        plan = DeploymentPlan(
            create=[DeltaCreate(meta, "url", "manifest")],
            patch=[
                DeltaPatch(meta, "", patch),
                DeltaPatch(meta, "  normal\n+  add\n-  remove", patch)
            ],
            delete=[DeltaDelete(meta, "url", "manifest")],
        )
        assert square.show_plan(plan) == (None, False)


class TestPartition:
    def test_partition_manifests_patch(self):
        """Local and server manifests match.

        If all resource exist both locally and remotely then nothing needs to
        be created or deleted. However, the resources may need patching but
        that is not something `partition_manifests` concerns itself with.

        """
        # Local and cluster manifests are identical - the Plan must not
        # create/add anything but mark all resources for (possible)
        # patching.
        local_man = cluster_man = {
            MetaManifest('v1', 'Namespace', None, 'ns3'): "0",
            MetaManifest('v1', 'Namespace', None, 'ns1'): "1",
            MetaManifest('v1', 'Deployment', 'ns2', 'bar'): "2",
            MetaManifest('v1', 'Namespace', None, 'ns2'): "3",
            MetaManifest('v1', 'Deployment', 'ns1', 'foo'): "4",
        }
        plan = DeploymentPlan(create=[], patch=list(local_man.keys()), delete=[])
        assert square.partition_manifests(local_man, cluster_man) == (plan, False)

    def test_partition_manifests_add_delete(self):
        """Local and server manifests are orthogonal sets.

        This must produce a plan where all local resources will be created, all
        cluster resources deleted and none patched.

        """
        fun = square.partition_manifests

        # Local and cluster manifests are orthogonal.
        local_man = {
            MetaManifest('v1', 'Deployment', 'ns2', 'bar'): "0",
            MetaManifest('v1', 'Namespace', None, 'ns2'): "1",
        }
        cluster_man = {
            MetaManifest('v1', 'Deployment', 'ns1', 'foo'): "2",
            MetaManifest('v1', 'Namespace', None, 'ns1'): "3",
            MetaManifest('v1', 'Namespace', None, 'ns3'): "4",
        }
        plan = DeploymentPlan(
            create=[
                MetaManifest('v1', 'Deployment', 'ns2', 'bar'),
                MetaManifest('v1', 'Namespace', None, 'ns2'),
            ],
            patch=[],
            delete=[
                MetaManifest('v1', 'Deployment', 'ns1', 'foo'),
                MetaManifest('v1', 'Namespace', None, 'ns1'),
                MetaManifest('v1', 'Namespace', None, 'ns3'),
            ]
        )
        assert fun(local_man, cluster_man) == (plan, False)

    def test_partition_manifests_patch_delete(self):
        """Create plan with resources to delete and patch.

        The local manifests are a strict subset of the cluster. The deployment
        plan must therefore not create any resources, delete everything absent
        from the local manifests and mark the rest for patching.

        """
        fun = square.partition_manifests

        # The local manifests are a subset of the server's. Therefore, the plan
        # must contain patches for those resources that exist locally and on
        # the server. All the other manifest on the server are obsolete.
        local_man = {
            MetaManifest('v1', 'Deployment', 'ns2', 'bar1'): "0",
            MetaManifest('v1', 'Namespace', None, 'ns2'): "1",
        }
        cluster_man = {
            MetaManifest('v1', 'Deployment', 'ns1', 'foo'): "2",
            MetaManifest('v1', 'Deployment', 'ns2', 'bar1'): "3",
            MetaManifest('v1', 'Deployment', 'ns2', 'bar2'): "4",
            MetaManifest('v1', 'Namespace', None, 'ns1'): "5",
            MetaManifest('v1', 'Namespace', None, 'ns2'): "6",
            MetaManifest('v1', 'Namespace', None, 'ns3'): "7",
        }
        plan = DeploymentPlan(
            create=[],
            patch=[
                MetaManifest('v1', 'Deployment', 'ns2', 'bar1'),
                MetaManifest('v1', 'Namespace', None, 'ns2'),
            ],
            delete=[
                MetaManifest('v1', 'Deployment', 'ns1', 'foo'),
                MetaManifest('v1', 'Deployment', 'ns2', 'bar2'),
                MetaManifest('v1', 'Namespace', None, 'ns1'),
                MetaManifest('v1', 'Namespace', None, 'ns3'),
            ]
        )
        assert fun(local_man, cluster_man) == (plan, False)


class TestPatchK8s:
    def test_make_patch_empty(self, k8sconfig):
        """Basic test: compute patch between two identical resources."""
        # Setup.
        kind, ns, name = 'Deployment', 'ns', 'foo'

        # PATCH URLs require the resource name at the end of the request path.
        url = urlpath(k8sconfig, kind, ns)[0] + f'/{name}'

        # The patch must be empty for identical manifests.
        loc = srv = make_manifest(kind, ns, name)
        data, err = square.make_patch(k8sconfig, loc, srv)
        assert (data, err) == (JsonPatch(url, []), False)
        assert isinstance(data, JsonPatch)

    def test_make_patch_incompatible(self, k8sconfig):
        """Must not try to compute diffs for incompatible manifests.

        For instance, refuse to compute a patch when one manifest has kind
        "Namespace" and the other "Deployment". The same is true for
        "apiVersion", "metadata.name" and "metadata.namespace".

        """
        # Demo Deployment manifest.
        srv = make_manifest('Deployment', 'Namespace', 'name')

        # `apiVersion` must match.
        loc = copy.deepcopy(srv)
        loc['apiVersion'] = 'mismatch'
        assert square.make_patch(k8sconfig, loc, srv) == (None, True)

        # `kind` must match.
        loc = copy.deepcopy(srv)
        loc['kind'] = 'Mismatch'
        assert square.make_patch(k8sconfig, loc, srv) == (None, True)

        # `name` must match.
        loc = copy.deepcopy(srv)
        loc['metadata']['name'] = 'mismatch'
        assert square.make_patch(k8sconfig, loc, srv) == (None, True)

        # `namespace` must match.
        loc = copy.deepcopy(srv)
        loc['metadata']['namespace'] = 'mismatch'
        assert square.make_patch(k8sconfig, loc, srv) == (None, True)

    def test_make_patch_special(self):
        """Namespace, ClusterRole(Bindings) etc are special.

        What makes them special is that they exist outside namespaces.
        Therefore, they will/must not contain a `metadata.Namespace` attribute
        and require special treatment in `make_patch`.

        """
        # Generic fixtures; values are irrelevant.
        config = types.SimpleNamespace(url='http://examples.com/', version="1.10")
        name = "foo"

        for kind in ["Namespace", "ClusterRole"]:
            # Determine the resource path so we can verify it later.
            url = urlpath(config, kind, None)[0] + f'/{name}'

            # The patch between two identical manifests must be empty but valid.
            loc = srv = make_manifest(kind, None, name)
            assert square.make_patch(config, loc, srv) == ((url, []), False)

            # Create two almost identical manifests, except the second one has
            # different `metadata.labels`. This must succeed.
            loc = make_manifest(kind, None, name)
            srv = copy.deepcopy(loc)
            loc['metadata']['labels'] = {"key": "value"}

            data, err = square.make_patch(config, loc, srv)
            assert err is False and len(data) > 0

    @mock.patch.object(k8s, "urlpath")
    def test_make_patch_error_urlpath(self, m_url, k8sconfig):
        """Coverage gap: simulate `urlpath` error."""
        # Setup.
        kind, ns, name = "Deployment", "ns", "foo"

        # Simulate `urlpath` error.
        m_url.return_value = (None, True)

        # Test function must return with error.
        loc = srv = make_manifest(kind, ns, name)
        assert square.make_patch(k8sconfig, loc, srv) == (None, True)


class TestPlan:
    def test_make_patch_ok(self, k8sconfig):
        """Compute patch between two manifests.

        This test function first verifies that the patch between two identical
        manifests is empty. The second used two manifests that have different
        labels. This must produce two patch operations, one to remove the old
        label and one to add the new ones.

        """
        # Two valid manifests.
        kind, namespace, name = "Deployment", "namespace", "name"
        srv = make_manifest(kind, namespace, name)
        loc = make_manifest(kind, namespace, name)
        srv["metadata"]["labels"] = {"old": "old"}
        loc["metadata"]["labels"] = {"new": "new"}

        # The Patch between two identical manifests must be a No-Op.
        expected = JsonPatch(
            url=urlpath(k8sconfig, kind, namespace)[0] + f"/{name}",
            ops=[],
        )
        assert square.make_patch(k8sconfig, loc, loc) == (expected, False)

        # The patch between `srv` and `loc` must remove the old label and add
        # the new one.
        expected = JsonPatch(
            url=urlpath(k8sconfig, kind, namespace)[0] + f"/{name}",
            ops=[
                {'op': 'remove', 'path': '/metadata/labels/old'},
                {'op': 'add', 'path': '/metadata/labels/new', 'value': 'new'}
            ]
        )
        assert square.make_patch(k8sconfig, loc, srv) == (expected, False)

    def test_make_patch_err(self, k8sconfig):
        """Verify error cases with invalid or incompatible manifests."""
        valid_cfg = k8sconfig
        invalid_cfg = k8sconfig._replace(version="invalid")

        # Create two valid manifests, then stunt one in such a way that
        # `manio.strip` will reject it.
        kind, namespace, name = "Deployment", "namespace", "name"
        valid = make_manifest(kind, namespace, name)
        invalid = make_manifest(kind, namespace, name)
        del invalid["kind"]

        # Must handle errors from `manio.strip`.
        assert square.make_patch(valid_cfg, valid, invalid) == (None, True)
        assert square.make_patch(valid_cfg, invalid, valid) == (None, True)
        assert square.make_patch(valid_cfg, invalid, invalid) == (None, True)

        # Must handle `urlpath` errors.
        assert square.make_patch(invalid_cfg, valid, valid) == (None, True)

        # Must handle incompatible manifests, ie manifests that do not belong
        # to the same resource.
        valid_a = make_manifest(kind, namespace, "bar")
        valid_b = make_manifest(kind, namespace, "foo")
        assert square.make_patch(valid_cfg, valid_a, valid_b) == (None, True)

    def test_compile_plan_create_delete_ok(self, k8sconfig):
        """Test a plan that creates and deletes resource, but not patches any.

        To do this, the local and server resources are all distinct. As a
        result, the returned plan must dictate that all local resources shall
        be created, all server resources deleted, and none patched.

        """
        # Allocate arrays for the MetaManifests and resource URLs.
        meta = [None] * 5
        url = [None] * 5

        # Define Namespace "ns1" with 1 deployment.
        meta[0] = MetaManifest('v1', 'Namespace', None, 'ns1')
        meta[1] = MetaManifest('v1', 'Deployment', 'ns1', 'res_0')

        # Define Namespace "ns2" with 2 deployments.
        meta[2] = MetaManifest('v1', 'Namespace', None, 'ns2')
        meta[3] = MetaManifest('v1', 'Deployment', 'ns2', 'res_1')
        meta[4] = MetaManifest('v1', 'Deployment', 'ns2', 'res_2')

        # Determine the K8s resource urls for those that will be added.
        upb = urlpath
        url[0] = upb(k8sconfig, meta[0].kind, meta[0].namespace)[0]
        url[1] = upb(k8sconfig, meta[1].kind, meta[1].namespace)[0]

        # Determine the K8s resource URLs for those that will be deleted. They
        # are slightly different because DELETE requests expect a URL path that
        # ends with the resource, eg
        # "/api/v1/namespaces/ns2"
        # instead of
        # "/api/v1/namespaces".
        url[2] = upb(k8sconfig, meta[2].kind, meta[2].namespace)[0] + "/" + meta[2].name
        url[3] = upb(k8sconfig, meta[3].kind, meta[3].namespace)[0] + "/" + meta[3].name
        url[4] = upb(k8sconfig, meta[4].kind, meta[4].namespace)[0] + "/" + meta[4].name

        # Compile local and server manifests that have no resource overlap.
        # This will ensure that we have to create all the local resources,
        # delete all the server resources and path nothing.
        loc_man = {meta[0]: "0", meta[1]: "1"}
        srv_man = {meta[2]: "2", meta[3]: "3", meta[4]: "4"}

        # The resources require a manifest to specify the terms of deletion.
        # This is currently hard coded into the function.
        del_opts = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "gracePeriodSeconds": 0,
            "orphanDependents": False,
        }

        # Resources from local files must be created, resources on server must
        # be deleted.
        expected = DeploymentPlan(
            create=[
                DeltaCreate(meta[0], url[0], loc_man[meta[0]]),
                DeltaCreate(meta[1], url[1], loc_man[meta[1]]),
            ],
            patch=[],
            delete=[
                DeltaDelete(meta[2], url[2], del_opts),
                DeltaDelete(meta[3], url[3], del_opts),
                DeltaDelete(meta[4], url[4], del_opts),
            ],
        )
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (expected, False)

    @mock.patch.object(square, "partition_manifests")
    def test_compile_plan_create_delete_err(self, m_part, k8sconfig):
        """Simulate `urlpath` errors"""
        # Invalid configuration. We will use it to trigger an error in `urlpath`.
        cfg_invalid = k8sconfig._replace(version="invalid")

        # Valid ManifestMeta and dummy manifest dict.
        meta = manio.make_meta(make_manifest("Deployment", "ns", "name"))
        man = {meta: None}

        # Pretend we only have to "create" resources, and then trigger the
        # `urlpath` error in its code path.
        m_part.return_value = (
            DeploymentPlan(create=[meta], patch=[], delete=[]),
            False,
        )
        assert square.compile_plan(cfg_invalid, man, man) == (None, True)

        # Pretend we only have to "delete" resources, and then trigger the
        # `urlpath` error in its code path.
        m_part.return_value = (
            DeploymentPlan(create=[], patch=[], delete=[meta]),
            False,
        )
        assert square.compile_plan(cfg_invalid, man, man) == (None, True)

    def test_compile_plan_patch_no_diff(self, k8sconfig):
        """Test a plan that patches no resources.

        To do this, the local and server resources are identical. As a
        result, the returned plan must nominate all manifests for patching, and
        none to create and delete.

        """
        # Allocate arrays for the MetaManifests.
        meta = [None] * 4

        # Define two namespaces with 1 deployment in each.
        meta[0] = MetaManifest('v1', 'Namespace', None, 'ns1')
        meta[1] = MetaManifest('v1', 'Deployment', 'ns1', 'res_0')
        meta[2] = MetaManifest('v1', 'Namespace', None, 'ns2')
        meta[3] = MetaManifest('v1', 'Deployment', 'ns2', 'res_1')

        # Local and server manifests are identical. The plan must therefore
        # only nominate patches but nothing to create or delete.
        loc_man = srv_man = {
            meta[0]: make_manifest("Namespace", None, "ns1"),
            meta[1]: make_manifest("Deployment", "ns1", "res_0"),
            meta[2]: make_manifest("Namespace", None, "ns2"),
            meta[3]: make_manifest("Deployment", "ns2", "res_1"),
        }
        expected = DeploymentPlan(create=[], patch=[], delete=[])
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (expected, False)

    def test_compile_plan_patch_with_diff(self, k8sconfig):
        """Test a plan that patches all resources.

        To do this, the local and server resources are identical. As a
        result, the returned plan must nominate all manifests for patching, and
        none to create and delete.

        """
        # Define a single resource.
        meta = MetaManifest('v1', 'Namespace', None, 'ns1')

        # Local and server manifests have the same resources but their
        # definition differs. This will ensure a non-empty patch in the plan.
        loc_man = {meta: make_manifest("Namespace", None, "ns1")}
        srv_man = {meta: make_manifest("Namespace", None, "ns1")}
        loc_man[meta]["metadata"]["labels"] = {"foo": "foo"}
        srv_man[meta]["metadata"]["labels"] = {"bar": "bar"}

        # Compute the JSON patch and textual diff to populated the expected
        # output structure below.
        patch, err = square.make_patch(k8sconfig, loc_man[meta], srv_man[meta])
        assert not err
        diff_str, err = manio.diff(k8sconfig, loc_man[meta], srv_man[meta])
        assert not err

        # Verify the test function returns the correct Patch and diff.
        expected = DeploymentPlan(
            create=[],
            patch=[DeltaPatch(meta, diff_str, patch)],
            delete=[]
        )
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (expected, False)

    @mock.patch.object(square, "partition_manifests")
    @mock.patch.object(manio, "diff")
    @mock.patch.object(square, "make_patch")
    def test_compile_plan_err(self, m_apply, m_plan, m_part, k8sconfig):
        """Use mocks for the internal function calls to simulate errors."""
        # Define a single resource and valid dummy return value for
        # `square.partition_manifests`.
        meta = MetaManifest('v1', 'Namespace', None, 'ns1')
        plan = DeploymentPlan(create=[], patch=[meta], delete=[])

        # Local and server manifests have the same resources but their
        # definition differs. This will ensure a non-empty patch in the plan.
        loc_man = srv_man = {meta: make_manifest("Namespace", None, "ns1")}

        # Simulate an error in `compile_plan`.
        m_part.return_value = (None, True)
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (None, True)

        # Simulate an error in `diff`.
        m_part.return_value = (plan, False)
        m_plan.return_value = (None, True)
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (None, True)

        # Simulate an error in `make_patch`.
        m_part.return_value = (plan, False)
        m_plan.return_value = ("some string", False)
        m_apply.return_value = (None, True)
        assert square.compile_plan(k8sconfig, loc_man, srv_man) == (None, True)


class TestMainOptions:
    @mock.patch.object(k8s, "post")
    @mock.patch.object(k8s, "patch")
    @mock.patch.object(k8s, "delete")
    def test_apply_plan(self, m_delete, m_apply, m_post, kube_creds):
        """Simulate a successful resource update (add, patch delete).

        To this end, create a valid (mocked) deployment plan, mock out all
        calls, and verify that all the necessary calls are made.

        The second part of the test simulates errors. This is not a separate
        test because it shares virtually all the boiler plate code.
        """
        # Valid MetaManifest.
        meta = manio.make_meta(make_manifest("Deployment", "ns", "name"))

        # Valid Patch.
        patch = JsonPatch(
            url="patch_url",
            ops=[
                {'op': 'remove', 'path': '/metadata/labels/old'},
                {'op': 'add', 'path': '/metadata/labels/new', 'value': 'new'},
            ],
        )

        # Valid non-empty deployment plan.
        plan = DeploymentPlan(
            create=[DeltaCreate(meta, "create_url", "create_man")],
            patch=[DeltaPatch(meta, "diff", patch)],
            delete=[DeltaDelete(meta, "delete_url", "delete_man")],
        )

        def reset_mocks():
            m_post.reset_mock()
            m_apply.reset_mock()
            m_delete.reset_mock()

            # Pretend that all K8s requests succeed.
            m_post.return_value = (None, False)
            m_apply.return_value = (None, False)
            m_delete.return_value = (None, False)

        # Update the K8s resources and verify that the test functions made the
        # corresponding calls to K8s.
        reset_mocks()
        assert square.apply_plan("kubeconfig", "kubectx", plan) == (None, False)
        m_post.assert_called_once_with("k8s_client", "create_url", "create_man")
        m_apply.assert_called_once_with("k8s_client", patch.url, patch.ops)
        m_delete.assert_called_once_with("k8s_client", "delete_url", "delete_man")

        # -----------------------------------------------------------------
        #                   Simulate An Empty Plan
        # -----------------------------------------------------------------
        # Repeat the test and ensure the function does not even ask for
        # confirmation if the plan is empty.
        reset_mocks()
        empty_plan = DeploymentPlan(create=[], patch=[], delete=[])

        # Call test function and verify that it did not try to apply
        # the empty plan.
        assert square.apply_plan("kubeconfig", "kubectx", empty_plan) == (None, False)
        assert not m_post.called
        assert not m_apply.called
        assert not m_delete.called

        # -----------------------------------------------------------------
        #                   Simulate Error Scenarios
        # -----------------------------------------------------------------
        reset_mocks()

        # Make `delete` fail.
        m_delete.return_value = (None, True)
        assert square.apply_plan("kubeconfig", "kubectx", plan) == (None, True)

        # Make `patch` fail.
        m_apply.return_value = (None, True)
        assert square.apply_plan("kubeconfig", "kubectx", plan) == (None, True)

        # Make `post` fail.
        m_post.return_value = (None, True)
        assert square.apply_plan("kubeconfig", "kubectx", plan) == (None, True)

    @mock.patch.object(manio, "load")
    @mock.patch.object(manio, "download")
    @mock.patch.object(manio, "align_serviceaccount")
    @mock.patch.object(square, "compile_plan")
    def test_make_plan(self, m_plan, m_align, m_down, m_load, kube_creds):
        """Basic test.

        This function does hardly anything to begin with, so we will merely
        verify it calls the correct functions with the correct arguments and
        handles errors correctly.

        """
        # Valid deployment plan.
        plan = DeploymentPlan(create=[], patch=[], delete=[])

        # All auxiliary functions will succeed.
        m_load.return_value = ("local", None, False)
        m_down.return_value = ("server", False)
        m_plan.return_value = (plan, False)
        m_align.side_effect = lambda loc_man, srv_man: (loc_man, False)

        # The arguments to the test function will always be the same in this test.
        selectors = Selectors(["kinds"], ["ns"], {("foo", "bar"), ("x", "y")})
        args = "kubeconf", "kubectx", "folder", selectors

        # A successful DIFF only computes and prints the plan.
        plan, err = square.make_plan(*args)
        assert not err and isinstance(plan, DeploymentPlan)
        m_load.assert_called_once_with("folder", selectors)
        m_down.assert_called_once_with("k8s_config", "k8s_client", selectors)
        m_plan.assert_called_once_with("k8s_config", "local", "server")

        # Make `compile_plan` fail.
        m_plan.return_value = (None, True)
        assert square.make_plan(*args) == (None, True)

        # Make `download_manifests` fail.
        m_down.return_value = (None, True)
        assert square.make_plan(*args) == (None, True)

        # Make `load` fail.
        m_load.return_value = (None, None, True)
        assert square.make_plan(*args) == (None, True)

    @mock.patch.object(manio, "load")
    @mock.patch.object(manio, "download")
    @mock.patch.object(manio, "sync")
    @mock.patch.object(manio, "save")
    def test_get_resources(self, m_save, m_sync, m_down, m_load, kube_creds):
        """Basic test.

        This function does hardly anything to begin with, so we will merely
        verify it calls the correct functions with the correct arguments and
        handles errors.

        """
        # Define a grouping (not relevant for this test but a necessary
        # argument to the test function).
        groupby = GroupBy(order=[], label="")

        # Simulate successful responses from the two auxiliary functions.
        # The `load` function must return empty dicts to ensure the error
        # conditions are properly coded.
        m_load.return_value = ("local", {}, False)
        m_down.return_value = ("server", False)
        m_sync.return_value = ("synced", False)
        m_save.return_value = (None, False)

        # The arguments to the test function will always be the same in this test.
        selectors = Selectors(["kinds"], ["ns"], {("foo", "bar"), ("x", "y")})
        args = "kubeconf", "kubectx", "folder", selectors, groupby

        # `manio.load` must have been called with a wildcard selector to ensure
        # it loads _all_ resources from the local files, even if we want to
        # sync only a subset.
        load_selectors = Selectors(kinds=SUPPORTED_KINDS, labels=None, namespaces=None)

        # Call test function and verify it passed the correct arguments.
        assert square.get_resources(*args) == (None, False)
        m_load.assert_called_once_with("folder", load_selectors)
        m_down.assert_called_once_with("k8s_config", "k8s_client", selectors)
        m_sync.assert_called_once_with({}, "server", selectors, groupby)
        m_save.assert_called_once_with("folder", "synced")

        # Simulate an error with `manio.save`.
        m_save.return_value = (None, True)
        assert square.get_resources(*args) == (None, True)

        # Simulate an error with `manio.sync`.
        m_sync.return_value = (None, True)
        assert square.get_resources(*args) == (None, True)

        # Simulate an error in `download_manifests`.
        m_down.return_value = (None, True)
        assert square.get_resources(*args) == (None, True)

        # Simulate an error in `load`.
        m_load.return_value = (None, None, True)
        assert square.get_resources(*args) == (None, True)
