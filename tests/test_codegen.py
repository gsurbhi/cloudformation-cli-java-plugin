# fixture and parameter have the same name
# pylint: disable=redefined-outer-name,protected-access
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import copyfile
from unittest.mock import MagicMock, Mock, patch

import yaml

import pytest
from rpdk.core.exceptions import InternalError, SysExitRecommendedError
from rpdk.core.project import Project
from rpdk.java.__init__ import __version__
from rpdk.java.codegen import (
    InvalidMavenPOMError,
    JavaArchiveNotFoundError,
    JavaLanguagePlugin,
    JavaPluginNotFoundError,
    JavaPluginVersionNotSupportedError,
)

RESOURCE = "DZQWCC"
HOOK = "CCWQZD"

TEST_TARGET_INFO = {
    "My::Example::Resource": {
        "TargetName": "My::Example::Resource",
        "TargetType": "RESOURCE",
        "Schema": {
            "typeName": "My::Example::Resource",
            "additionalProperties": False,
            "properties": {
                "Id": {"type": "string"},
                "Tags": {
                    "type": "array",
                    "uniqueItems": False,
                    "items": {"$ref": "#/definitions/Tag"},
                },
            },
            "required": [],
            "definitions": {
                "Tag": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "Value": {"type": "string"},
                        "Key": {"type": "string"},
                    },
                    "required": ["Value", "Key"],
                }
            },
        },
        "ProvisioningType": "FULLY_MUTTABLE",
        "IsCfnRegistrySupportedType": True,
        "SchemaFileAvailable": True,
    },
    "My::Other::Resource": {
        "TargetName": "My::Other::Resource",
        "TargetType": "RESOURCE",
        "Schema": {
            "typeName": "My::Other::Resource",
            "additionalProperties": False,
            "properties": {
                "Id": {"type": "string"},
                "Tags": {
                    "type": "array",
                    "uniqueItems": False,
                    "items": {"$ref": "#/definitions/Tag"},
                },
            },
            "required": [],
            "definitions": {
                "Tag": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "Value": {"type": "string"},
                        "Key": {"type": "string"},
                    },
                    "required": ["Value", "Key"],
                }
            },
        },
        "ProvisioningType": "NOT_PROVISIONABLE",
        "IsCfnRegistrySupportedType": False,
        "SchemaFileAvailable": True,
    },
}


@pytest.fixture(params=["1", "2"])
def project(tmpdir, request):
    def mock_input_with_validation(prompt, validate):  # pylint: disable=unused-argument
        if prompt.startswith("Enter a package name"):
            return ("software", "amazon", "foo", RESOURCE.lower())
        if prompt.startswith("Choose codegen model"):
            return request.param
        return ""

    project = Project(root=tmpdir)
    mock_cli = MagicMock(side_effect=mock_input_with_validation)
    with patch.dict(
        "rpdk.core.plugin_registry.PLUGIN_REGISTRY",
        {"test": lambda: JavaLanguagePlugin},
        clear=True,
    ), patch("rpdk.java.codegen.input_with_validation", new=mock_cli):
        project.init("AWS::Foo::{}".format(RESOURCE), "test")
    return project


@pytest.fixture(params=["1", "2"])
def hook_project(tmpdir, request):
    def mock_input_with_validation(prompt, validate):  # pylint: disable=unused-argument
        if prompt.startswith("Enter a package name"):
            return ("software", "amazon", "foo", HOOK.lower())
        if prompt.startswith("Choose codegen model"):
            return request.param
        return ""

    hook_project = Project(root=tmpdir)
    mock_cli = MagicMock(side_effect=mock_input_with_validation)
    with patch.dict(
        "rpdk.core.plugin_registry.PLUGIN_REGISTRY",
        {"test": lambda: JavaLanguagePlugin},
        clear=True,
    ), patch("rpdk.java.codegen.input_with_validation", new=mock_cli):
        hook_project.init_hook("AWS::Foo::{}".format(RESOURCE), "test")
    return hook_project


def test_java_language_plugin_module_is_set():
    plugin = JavaLanguagePlugin()
    assert plugin.MODULE_NAME


def test_initialize(project):
    expected_group_id = "software.amazon.foo.{}".format(RESOURCE.lower())
    handler = "{}.HandlerWrapper::handleRequest".format(expected_group_id)
    assert_test_initialize(project, handler, expected_group_id)


def test_hook_initialize(hook_project):
    expected_group_id = "software.amazon.foo.{}".format(HOOK.lower())
    handler = "{}.HookHandlerWrapper::handleRequest".format(expected_group_id)
    assert_test_initialize(hook_project, handler, expected_group_id)


def assert_test_initialize(
    test_project, handler, expected_group_id
):  # pylint: disable=protected-access
    assert (test_project.root / "README.md").is_file()

    pom_tree = ET.parse(str(test_project.root / "pom.xml"))
    namespace = {"maven": "http://maven.apache.org/POM/4.0.0"}
    actual_group_id = pom_tree.find("maven:groupId", namespace)
    assert actual_group_id.text == expected_group_id
    path = test_project.root / "template.yml"
    with path.open("r", encoding="utf-8") as f:
        template = yaml.safe_load(f)
    handler_properties = template["Resources"]["TypeFunction"]["Properties"]
    code_uri = "./target/{}-handler-1.0-SNAPSHOT.jar".format(
        test_project.hypenated_name
    )
    assert handler_properties["CodeUri"] == code_uri
    assert handler_properties["Handler"] == handler
    assert handler_properties["Runtime"] == test_project._plugin.RUNTIME


def test_generate(project):
    project.load_schema()
    assert_generate_test(project, RESOURCE)


def test_hook_generate(hook_project):
    with patch.object(hook_project, "_load_target_info", return_value=TEST_TARGET_INFO):
        hook_project.load_hook_schema()
        assert_generate_test(hook_project, HOOK)


def assert_generate_test(test_project, test_type):  # pylint: disable=protected-access
    generated_root = test_project._plugin._get_generated_root(test_project)
    generated_tests_root = test_project._plugin._get_generated_tests_root(test_project)

    # generated root shouldn't be present
    assert not generated_root.is_dir()
    assert not generated_tests_root.is_dir()

    test_project.generate()

    src_file = generated_root / "test"
    src_file.touch()

    test_file = generated_tests_root / "test"
    test_file.touch()

    test_project.generate()

    # assert TypeConfigurationModel is added to generated directory
    type_configuration_model_file = (
        generated_root
        / "software"
        / "amazon"
        / "foo"
        / test_type.lower()
        / "TypeConfigurationModel.java"
    )
    assert type_configuration_model_file.is_file()

    # asserts we remove existing files in the tree
    assert not src_file.is_file()
    assert not test_file.is_file()


def test_generate_with_type_configuration(project, tmpdir):
    copyfile(
        str(Path.cwd() / "tests/data/schema-with-typeconfiguration.json"),
        str(tmpdir / "schema-with-typeconfiguration.json"),
    )
    project.type_info = ("schema", "with", "typeconfiguration")
    project.load_schema()
    project.load_configuration_schema()
    project.generate()
    generated_root = project._plugin._get_generated_root(project)

    # assert TypeConfigurationModel is added to generated directory
    type_configuration_model_file = (
        generated_root
        / "software"
        / "amazon"
        / "foo"
        / RESOURCE.lower()
        / "TypeConfigurationModel.java"
    )
    type_configuration_schema_file = (
        generated_root / "schema-with-typeconfiguration-configuration.json"
    )

    assert type_configuration_model_file.is_file()
    assert type_configuration_schema_file.is_file()


def test_generate_with_out_type_configuration(project, tmpdir):
    copyfile(
        str(Path.cwd() / "tests/data/schema-without-typeconfiguration.json"),
        str(tmpdir / "schema-without-typeconfiguration.json"),
    )
    project.type_info = ("schema", "without", "typeconfiguration")
    project.load_schema()
    project.load_configuration_schema()
    project.generate()
    generated_root = project._plugin._get_generated_root(project)

    # assert TypeConfigurationModel is added to generated directory
    type_configuration_model_file = (
        generated_root
        / "software"
        / "amazon"
        / "foo"
        / RESOURCE.lower()
        / "TypeConfigurationModel.java"
    )
    type_configuration_schema_file = (
        generated_root / "schema-without-typeconfiguration-configuration.json"
    )

    assert type_configuration_model_file.is_file()
    assert not type_configuration_schema_file.is_file()


def test_hook_generate_with_type_configuration(hook_project, tmpdir):
    copyfile(
        str(Path.cwd() / "tests/data/hook-schema-with-typeconfiguration.json"),
        str(tmpdir / "hook-schema-with-typeconfiguration.json"),
    )

    with patch.object(hook_project, "_load_target_info", return_value=TEST_TARGET_INFO):
        hook_project.type_info = ("hook", "schema", "with", "typeconfiguration")
        hook_project.load_hook_schema()
        hook_project.load_configuration_schema()
        hook_project.generate()
        generated_root = hook_project._plugin._get_generated_root(hook_project)

    # assert TypeConfigurationModel is added to generated directory
    type_configuration_model_file = (
        generated_root
        / "software"
        / "amazon"
        / "foo"
        / HOOK.lower()
        / "TypeConfigurationModel.java"
    )
    type_configuration_schema_file = (
        generated_root / "hook-schema-with-typeconfiguration-configuration.json"
    )

    assert type_configuration_model_file.is_file()
    assert type_configuration_schema_file.is_file()


def test_hook_generate_with_non_registry_targets(hook_project, tmpdir):
    copyfile(
        str(Path.cwd() / "tests/data/hook-schema-with-typeconfiguration.json"),
        str(tmpdir / "hook-schema-with-typeconfiguration.json"),
    )

    test_target_info = dict(TEST_TARGET_INFO)
    test_target_info["My::Unreleased::Resource"] = {
        "TargetName": "My::Unreleased::Resource",
        "TargetType": "RESOURCE",
        "Schema": {},
        "ProvisioningType": "NOT_PROVISIONABLE",
        "IsCfnRegistrySupportedType": False,
        "SchemaFileAvailable": False,
    }

    with patch.object(hook_project, "_load_target_info", return_value=test_target_info):
        hook_project.type_info = ("hook", "schema", "with", "typeconfiguration")
        hook_project.load_hook_schema()
        hook_project.load_configuration_schema()
        hook_project.generate()
        generated_root = hook_project._plugin._get_generated_root(hook_project)

    # assert TypeConfigurationModel is added to generated directory
    non_registry_target_model_file = (
        generated_root
        / "software"
        / "amazon"
        / "foo"
        / HOOK.lower()
        / "model"
        / "my"
        / "unreleased"
        / "resource"
        / "MyUnreleasedResourceTargetModel.java"
    )

    assert non_registry_target_model_file.is_file()


def test_protocol_version_is_set(project):
    assert project.settings["protocolVersion"] == "2.0.0"


def test_generate_low_protocol_version_is_updated(project):
    project.settings["protocolVersion"] = "1.0.0"
    project.generate()
    assert project.settings["protocolVersion"] == "2.0.0"


def update_pom_with_plugin_version(project, version_id):
    pom_tree = ET.parse(project.root / "pom.xml")
    root = pom_tree.getroot()
    namespace = {"mvn": "http://maven.apache.org/POM/4.0.0"}
    version = root.find(
        "./mvn:dependencies/mvn:dependency"
        "/[mvn:artifactId='aws-cloudformation-rpdk-java-plugin']/mvn:version",
        namespace,
    )
    version.text = version_id
    pom_tree.write(project.root / "pom.xml")


def test_generate_with_not_support_version(project):
    update_pom_with_plugin_version(project, "1.0.0")

    with pytest.raises(JavaPluginVersionNotSupportedError):
        project.generate()


def make_target(project, count):
    target = project.root / "target"
    target.mkdir(exist_ok=True)
    jar_paths = []
    for i in range(count):
        jar_path = target / "{}-{}.0-SNAPSHOT.jar".format(project.hypenated_name, i)
        jar_path.touch()
        jar_paths.append(jar_path)
    return jar_paths


def test__find_jar_zero(project):
    make_target(project, 0)
    with pytest.raises(JavaArchiveNotFoundError) as excinfo:
        project._plugin._find_jar(project)

    assert isinstance(excinfo.value, SysExitRecommendedError)


def test__find_jar_one(project):
    jar_path, *_ = make_target(project, 1)
    assert project._plugin._find_jar(project) == jar_path


def test__find_jar_two(project):
    make_target(project, 2)
    with pytest.raises(InternalError):
        project._plugin._find_jar(project)


def make_pom_xml_without_plugin(project):
    pom_tree = ET.parse(project.root / "pom.xml")
    root = pom_tree.getroot()
    namespace = {"mvn": "http://maven.apache.org/POM/4.0.0"}
    plugin = root.find(
        ".//mvn:dependency/[mvn:artifactId='aws-cloudformation-rpdk-java-plugin']",
        namespace,
    )
    dependencies = root.find("mvn:dependencies", namespace)
    dependencies.remove(plugin)
    pom_tree.write(project.root / "pom.xml")


def test__get_plugin_version_not_found(project):
    make_pom_xml_without_plugin(project)
    with pytest.raises(JavaPluginNotFoundError):
        project._plugin._get_java_plugin_dependency_version(project)


def test_generate_without_java_plugin_in_pom_should_not_fail(project):
    make_pom_xml_without_plugin(project)
    project.generate()
    assert project.settings["protocolVersion"] == "2.0.0"


def test__get_plugin_version_invalid_pom(project):
    with open(project.root / "pom.xml", "w") as pom:
        pom.write("invalid pom")
        pom.close()
    with pytest.raises(InvalidMavenPOMError):
        project._plugin._get_java_plugin_dependency_version(project)


def test_package(project):
    project.load_schema()
    project.generate()
    make_target(project, 1)

    zip_file = Mock()
    project._plugin.package(project, zip_file)

    writes = []
    for call in zip_file.write.call_args_list:
        args, _kwargs = call
        writes.append(str(args[1]))  # relative path

    assert len(writes) > 10
    assert "pom.xml" in writes


def test__prompt_for_namespace_aws_default():
    project = Mock(type_info=("AWS", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch("rpdk.core.utils.init_utils.input", return_value="") as mock_input:
        plugin._prompt_for_namespace(project)

    mock_input.assert_called_once()

    assert project.settings == {"namespace": ("software", "amazon", "clown", "service")}


def test__prompt_for_namespace_aws_overwritten():
    project = Mock(type_info=("AWS", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch(
        "rpdk.core.utils.init_utils.input", return_value="com.red.clown.service"
    ) as mock_input:
        plugin._prompt_for_namespace(project)

    mock_input.assert_called_once()

    assert project.settings == {"namespace": ("com", "red", "clown", "service")}


def test__prompt_for_namespace_other_default():
    project = Mock(type_info=("Balloon", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch("rpdk.core.utils.init_utils.input", return_value="") as mock_input:
        plugin._prompt_for_namespace(project)

    mock_input.assert_called_once()

    assert project.settings == {"namespace": ("com", "balloon", "clown", "service")}


def test__prompt_for_namespace_other_overwritten():
    project = Mock(type_info=("Balloon", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch(
        "rpdk.core.utils.init_utils.input", return_value="com.ball.clown.service"
    ) as mock_input:
        plugin._prompt_for_namespace(project)

    mock_input.assert_called_once()

    assert project.settings == {"namespace": ("com", "ball", "clown", "service")}


def test__namespace_from_project_new_settings():
    namespace = ("com", "ball", "clown", "service")
    project = Mock(settings={"namespace": namespace})
    plugin = JavaLanguagePlugin()
    plugin._namespace_from_project(project)

    assert plugin.namespace == namespace
    assert plugin.package_name == "com.ball.clown.service"


def test__namespace_from_project_old_settings():
    project = Mock(type_info=("Balloon", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()
    plugin._namespace_from_project(project)

    assert plugin.namespace == ("com", "balloon", "clown", "service")
    assert plugin.package_name == "com.balloon.clown.service"


def test__prompt_for_codegen_model_no_selection():
    project = Mock(type_info=("AWS", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch("rpdk.core.utils.init_utils.input", return_value="") as mock_input:
        plugin._prompt_for_codegen_model(project)

    mock_input.assert_called_once()

    assert project.settings == {"codegen_template_path": "default"}


def test__prompt_for_codegen_model_default():
    project = Mock(type_info=("AWS", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch("rpdk.core.utils.init_utils.input", return_value="1") as mock_input:
        plugin._prompt_for_codegen_model(project)

    mock_input.assert_called_once()

    assert project.settings == {"codegen_template_path": "default"}


def test__prompt_for_codegen_model_guided_aws():
    project = Mock(type_info=("AWS", "Clown", "Service"), settings={})
    plugin = JavaLanguagePlugin()

    with patch("rpdk.core.utils.init_utils.input", return_value="2") as mock_input:
        plugin._prompt_for_codegen_model(project)

    mock_input.assert_called_once()

    assert project.settings == {"codegen_template_path": "guided_aws"}


def test_generate_image_build_config(project):
    make_target(project, 1)

    config = project._plugin.generate_image_build_config(project)

    assert "executable_name" in config
    assert "project_path" in config
    assert "dockerfile_path" in config


def test_generate_executable_entrypoint_specified(project):
    project.executable_entrypoint = "entrypoint"
    project.generate()
    assert project.executable_entrypoint == "entrypoint"


def test_generate_executable_entrypoint_not_specified(project):
    project.executable_entrypoint = None
    project.generate()
    plugin = JavaLanguagePlugin()
    plugin._namespace_from_project(project)

    assert (
        project.executable_entrypoint
        == plugin.package_name + ".HandlerWrapperExecutable"
    )


def test_generate_hook_executable_entrypoint_not_specified(hook_project):
    hook_project.executable_entrypoint = None
    with patch.object(hook_project, "_load_target_info", return_value=TEST_TARGET_INFO):
        hook_project.generate()
    plugin = JavaLanguagePlugin()
    plugin._namespace_from_project(hook_project)

    assert (
        hook_project.executable_entrypoint
        == plugin.package_name + ".HookHandlerWrapperExecutable"
    )


def test_generate_executable_entrypoint_old_project_version(project):
    # If the cli version does not contain the new executable_entrypoint
    # we will not add it
    del project.executable_entrypoint
    project.generate()
    plugin = JavaLanguagePlugin()
    plugin._namespace_from_project(project)

    assert not hasattr(project, "executable_entrypoint")


def test_get_plugin_information(project):
    plugin_information = project._plugin.get_plugin_information(project)

    assert plugin_information["plugin-tool-version"] == __version__
    assert plugin_information["plugin-name"] == "java"
    assert plugin_information[
        "plugin-version"
    ] == JavaLanguagePlugin._get_java_plugin_dependency_version(project)
