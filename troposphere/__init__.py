# Copyright (c) 2012-2013, Mark Peek <mark@peek.org>
# All rights reserved.
#
# See LICENSE file for full license.


import collections
import json
import re
import sys
import types

from . import validators

__version__ = "1.9.1"

# constants for DeletionPolicy
Delete = 'Delete'
Retain = 'Retain'
Snapshot = 'Snapshot'

# Pseudo Parameters
AWS_ACCOUNT_ID = 'AWS::AccountId'
AWS_NOTIFICATION_ARNS = 'AWS::NotificationARNs'
AWS_NO_VALUE = 'AWS::NoValue'
AWS_REGION = 'AWS::Region'
AWS_STACK_ID = 'AWS::StackId'
AWS_STACK_NAME = 'AWS::StackName'

# Template Limits
MAX_PARAMETERS = 60
MAX_RESOURCES = 200
PARAMETER_TITLE_MAX = 255

valid_names = re.compile(r'^[a-zA-Z0-9]+$')


def is_aws_object_subclass(cls):
    is_aws_object = False
    try:
        is_aws_object = issubclass(cls, BaseAWSObject)
    # prop_type isn't a class
    except TypeError:
        pass
    return is_aws_object


class BaseAWSObject(object):
    def __init__(self, title, template=None, **kwargs):
        self.title = title
        self.template = template
        # Cache the keys for validity checks
        self.propnames = self.props.keys()
        self.attributes = ['DependsOn', 'DeletionPolicy',
                           'Metadata', 'UpdatePolicy',
                           'Condition', 'CreationPolicy']

        # try to validate the title if its there
        if self.title:
            self.validate_title()

        # Create the list of properties set on this object by the user
        self.properties = {}
        dictname = getattr(self, 'dictname', None)
        if dictname:
            self.resource = {
                dictname: self.properties,
            }
        else:
            self.resource = self.properties
        if hasattr(self, 'resource_type') and self.resource_type is not None:
            self.resource['Type'] = self.resource_type
        self.__initialized = True

        # Check for properties defined in the class
        for k, (_, required) in self.props.items():
            v = getattr(type(self), k, None)
            if v is not None and k not in kwargs:
                self.__setattr__(k, v)

        # Now that it is initialized, populate it with the kwargs
        for k, v in kwargs.items():
            self.__setattr__(k, v)

        # Bound it to template if we know it
        if self.template is not None:
            self.template.add_resource(self)

    def __getattr__(self, name):
        try:
            return self.properties.__getitem__(name)
        except KeyError:
            # Fall back to the name attribute in the object rather than
            # in the properties dict. This is for non-OpenStack backwards
            # compatibility since OpenStack objects use a "name" property.
            if name == 'name':
                return self.__getattribute__('title')
            raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in self.__dict__.keys() \
                or '_BaseAWSObject__initialized' not in self.__dict__:
            return dict.__setattr__(self, name, value)
        elif name in self.attributes:
            self.resource[name] = value
            return None
        elif name in self.propnames:
            # Check the type of the object and compare against what we were
            # expecting.
            expected_type = self.props[name][0]

            # If the value is a AWSHelperFn we can't do much validation
            # we'll have to leave that to Amazon.  Maybe there's another way
            # to deal with this that we'll come up with eventually
            if isinstance(value, AWSHelperFn):
                return self.properties.__setitem__(name, value)

            # If it's a function, call it...
            elif isinstance(expected_type, types.FunctionType):
                try:
                    value = expected_type(value)
                except:
                    sys.stderr.write(
                        "%s: %s.%s function validator '%s' threw "
                        "exception:\n" % (self.__class__,
                                          self.title,
                                          name,
                                          expected_type.__name__))
                    raise
                return self.properties.__setitem__(name, value)

            # If it's a list of types, check against those types...
            elif isinstance(expected_type, list):
                # If we're expecting a list, then make sure it is a list
                if not isinstance(value, list):
                    self._raise_type(name, value, expected_type)

                # Iterate over the list and make sure it matches our
                # type checks (as above accept AWSHelperFn because
                # we can't do the validation ourselves)
                for v in value:
                    if not isinstance(v, tuple(expected_type)) \
                       and not isinstance(v, AWSHelperFn):
                        self._raise_type(name, v, expected_type)
                # Validated so assign it
                return self.properties.__setitem__(name, value)

            # Single type so check the type of the object and compare against
            # what we were expecting. Special case AWS helper functions.
            elif isinstance(value, expected_type):
                return self.properties.__setitem__(name, value)
            else:
                self._raise_type(name, value, expected_type)

        type_name = getattr(self, 'resource_type', self.__class__.__name__)

        if type_name == 'AWS::CloudFormation::CustomResource' or \
                type_name.startswith('Custom::'):
            # Add custom resource arguments to the dict without any further
            # validation. The properties of a CustomResource is not known.
            return self.properties.__setitem__(name, value)

        raise AttributeError("%s object does not support attribute %s" %
                             (type_name, name))

    def _raise_type(self, name, value, expected_type):
        raise TypeError('%s: %s.%s is %s, expected %s' % (self.__class__,
                                                          self.title,
                                                          name,
                                                          type(value),
                                                          expected_type))

    def validate_title(self):
        if not valid_names.match(self.title):
            raise ValueError('Name "%s" not alphanumeric' % self.title)

    def validate(self):
        pass

    @classmethod
    def _from_dict(cls, title=None, **kwargs):
        props = {}
        for prop_name, value in kwargs.items():
            try:
                prop_attrs = cls.props[prop_name]
            except KeyError:
                raise AttributeError("Object type %s does not have a "
                                     "%s property." % (cls.__name__,
                                                       prop_name))
            prop_type = prop_attrs[0]
            value = kwargs[prop_name]
            is_aws_object = is_aws_object_subclass(prop_type)
            if is_aws_object:
                if not isinstance(value, collections.Mapping):
                    raise ValueError("Property definition for %s must be "
                                     "a Mapping type" % prop_name)
                value = prop_type._from_dict(**value)

            if isinstance(prop_type, list):
                if not isinstance(value, list):
                    raise TypeError("Attribute %s must be a "
                                    "list." % prop_name)
                new_value = []
                for v in value:
                    new_v = v
                    if is_aws_object_subclass(prop_type[0]):
                        if not isinstance(v, collections.Mapping):
                            raise ValueError(
                                "Property definition for %s must be "
                                "a list of Mapping types" % prop_name)
                        new_v = prop_type[0]._from_dict(**v)
                    new_value.append(new_v)
                value = new_value
            props[prop_name] = value
        if title:
            return cls(title, **props)
        return cls(**props)

    @classmethod
    def from_dict(cls, title, d):
        return cls._from_dict(title, **d)

    def JSONrepr(self):
        for k, (_, required) in self.props.items():
            if required and k not in self.properties:
                rtype = getattr(self, 'resource_type', "<unknown type>")
                title = getattr(self, 'title')
                msg = "Resource %s required in type %s" % (k, rtype)
                if title:
                    msg += " (title: %s)" % title
                raise ValueError(msg)

        self.validate()
        # Mainly used to not have an empty "Properties".
        if self.properties:
            return self.resource
        elif hasattr(self, 'resource_type'):
            d = {}
            for k, v in self.resource.items():
                if k != 'Properties':
                    d[k] = v
            return d
        else:
            return {}


class AWSObject(BaseAWSObject):
    dictname = 'Properties'


class AWSDeclaration(BaseAWSObject):
    """
    Used for CloudFormation Resource Property objects
    http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/
    aws-product-property-reference.html
    """

    def __init__(self, title, **kwargs):
        super(AWSDeclaration, self).__init__(title, **kwargs)


class AWSProperty(BaseAWSObject):
    """
    Used for CloudFormation Resource Property objects
    http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/
    aws-product-property-reference.html
    """
    dictname = None

    def __init__(self, title=None, **kwargs):
        super(AWSProperty, self).__init__(title, **kwargs)


class AWSAttribute(BaseAWSObject):
    dictname = None

    """
    Used for CloudFormation Resource Attribute objects
    http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/
    aws-product-attribute-reference.html
    """

    def __init__(self, title=None, **kwargs):
        super(AWSAttribute, self).__init__(title, **kwargs)


def validate_delimiter(delimiter):
    if not isinstance(delimiter, basestring):
        raise ValueError(
            "Delimiter must be a String, %s provided" % type(delimiter)
        )


def validate_pausetime(pausetime):
    if not pausetime.startswith('PT'):
        raise ValueError('PauseTime should look like PT#H#M#S')
    return pausetime


class UpdatePolicy(BaseAWSObject):
    def __init__(self, title, **kwargs):
        raise DeprecationWarning(
            "This UpdatePolicy class is deprecated, please switch to using "
            "the more general UpdatePolicy in troposphere.policies.\n"
        )


class AWSHelperFn(object):
    def getdata(self, data):
        if isinstance(data, BaseAWSObject):
            return data.title
        else:
            return data


class GenericHelperFn(AWSHelperFn):
    """ Used as a fallback for the template generator """
    def __init__(self, data):
        self.data = self.getdata(data)

    def JSONrepr(self):
        return self.data


class Base64(AWSHelperFn):
    def __init__(self, data):
        self.data = {'Fn::Base64': data}

    def JSONrepr(self):
        return self.data


class FindInMap(AWSHelperFn):
    def __init__(self, mapname, key, value):
        self.data = {'Fn::FindInMap': [self.getdata(mapname), key, value]}

    def JSONrepr(self):
        return self.data


class GetAtt(AWSHelperFn):
    def __init__(self, logicalName, attrName):
        self.data = {'Fn::GetAtt': [self.getdata(logicalName), attrName]}

    def JSONrepr(self):
        return self.data


class GetAZs(AWSHelperFn):
    def __init__(self, region=""):
        self.data = {'Fn::GetAZs': region}

    def JSONrepr(self):
        return self.data


class If(AWSHelperFn):
    def __init__(self, cond, true, false):
        self.data = {'Fn::If': [self.getdata(cond), true, false]}

    def JSONrepr(self):
        return self.data


class Equals(AWSHelperFn):
    def __init__(self, value_one, value_two):
        self.data = {'Fn::Equals': [value_one, value_two]}

    def JSONrepr(self):
        return self.data


class And(AWSHelperFn):
    def __init__(self, cond_one, cond_two, *conds):
        self.data = {'Fn::And': [cond_one, cond_two] + list(conds)}

    def JSONrepr(self):
        return self.data


class Or(AWSHelperFn):
    def __init__(self, cond_one, cond_two, *conds):
        self.data = {'Fn::Or': [cond_one, cond_two] + list(conds)}

    def JSONrepr(self):
        return self.data


class Not(AWSHelperFn):
    def __init__(self, cond):
        self.data = {'Fn::Not': [self.getdata(cond)]}

    def JSONrepr(self):
        return self.data


class Join(AWSHelperFn):
    def __init__(self, delimiter, values):
        validate_delimiter(delimiter)
        self.data = {'Fn::Join': [delimiter, values]}

    def JSONrepr(self):
        return self.data


class Split(AWSHelperFn):
    def __init__(self, delimiter, values):
        validate_delimiter(delimiter)
        self.data = {'Fn::Split': [delimiter, values]}

    def JSONrepr(self):
        return self.data


class Sub(AWSHelperFn):
    def __init__(self, input_str, **values):
        self.data = {'Fn::Sub': [input_str, values] if values else input_str}

    def JSONrepr(self):
        return self.data


class Name(AWSHelperFn):
    def __init__(self, data):
        self.data = self.getdata(data)

    def JSONrepr(self):
        return self.data


class Select(AWSHelperFn):
    def __init__(self, indx, objects):
        self.data = {'Fn::Select': [indx, objects]}

    def JSONrepr(self):
        return self.data


class Ref(AWSHelperFn):
    def __init__(self, data):
        self.data = {'Ref': self.getdata(data)}

    def JSONrepr(self):
        return self.data


class Condition(AWSHelperFn):
    def __init__(self, data):
        self.data = {'Condition': self.getdata(data)}

    def JSONrepr(self):
        return self.data


class ImportValue(AWSHelperFn):
    def __init__(self, data):
        self.data = {'Fn::ImportValue': data}

    def JSONrepr(self):
        return self.data


class awsencode(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'JSONrepr'):
            return obj.JSONrepr()
        return json.JSONEncoder.default(self, obj)


class Tags(AWSHelperFn):
    def __init__(self, **kwargs):
        self.tags = []
        for k, v in sorted(kwargs.iteritems()):
            self.tags.append({
                'Key': k,
                'Value': v,
            })

    # allow concatenation of the Tags object via '+' operator
    def __add__(self, newtags):
        newtags.tags = self.tags + newtags.tags
        return newtags

    def JSONrepr(self):
        return self.tags


class Template(object):
    props = {
        'AWSTemplateFormatVersion': (basestring, False),
        'Description': (basestring, False),
        'Parameters': (dict, False),
        'Mappings': (dict, False),
        'Resources': (dict, False),
        'Outputs': (dict, False),
    }

    def __init__(self, Description=None, Metadata=None):
        self.description = Description
        self.metadata = {} if Metadata is None else Metadata
        self.conditions = {}
        self.mappings = {}
        self.outputs = {}
        self.parameters = {}
        self.resources = {}
        self.version = None

    def add_description(self, description):
        self.description = description

    def add_metadata(self, metadata):
        self.metadata = metadata

    def add_condition(self, name, condition):
        if len(self.conditions) >= 60:
            raise ValueError('Maximum of 60 conditions per template reached')

        self.conditions[name] = condition

    def handle_duplicate_key(self, key):
        raise ValueError('duplicate key "%s" detected' % key)

    def _update(self, d, values):
        if isinstance(values, list):
            for v in values:
                if v.title in d:
                    self.handle_duplicate_key(v.title)
                d[v.title] = v
        else:
            if values.title in d:
                self.handle_duplicate_key(values.title)
            d[values.title] = values
        return values

    def add_output(self, output):
        return self._update(self.outputs, output)

    def add_mapping(self, name, mapping):
        self.mappings[name] = mapping

    def add_parameter(self, parameter):
        if len(self.parameters) >= MAX_PARAMETERS:
            raise ValueError('Maximum parameters %d reached' % MAX_PARAMETERS)
        return self._update(self.parameters, parameter)

    def add_resource(self, resource):
        if len(self.resources) >= MAX_RESOURCES:
            raise ValueError('Maximum number of resources %d reached'
                             % MAX_RESOURCES)
        return self._update(self.resources, resource)

    def add_version(self, version=None):
        if version:
            self.version = version
        else:
            self.version = "2010-09-09"

    def to_json(self, indent=4, sort_keys=True, separators=(',', ': ')):
        t = {}
        if self.description:
            t['Description'] = self.description
        if self.metadata:
            t['Metadata'] = self.metadata
        if self.conditions:
            t['Conditions'] = self.conditions
        if self.mappings:
            t['Mappings'] = self.mappings
        if self.outputs:
            t['Outputs'] = self.outputs
        if self.parameters:
            t['Parameters'] = self.parameters
        if self.version:
            t['AWSTemplateFormatVersion'] = self.version
        t['Resources'] = self.resources

        return json.dumps(t, cls=awsencode, indent=indent,
                          sort_keys=sort_keys, separators=separators)

    def JSONrepr(self):
        return [self.parameters, self.mappings, self.resources]


class Export(AWSHelperFn):
    def __init__(self, name):
        self.data = {
            'Name': name,
        }

    def JSONrepr(self):
        return self.data


class Output(AWSDeclaration):
    props = {
        'Description': (basestring, False),
        'Export': (Export, False),
        'Value': (basestring, True),
    }


class Parameter(AWSDeclaration):
    STRING_PROPERTIES = ['AllowedPattern', 'MaxLength', 'MinLength']
    NUMBER_PROPERTIES = ['MaxValue', 'MinValue']
    props = {
        'Type': (basestring, True),
        'Default': (basestring, False),
        'NoEcho': (bool, False),
        'AllowedValues': (list, False),
        'AllowedPattern': (basestring, False),
        'MaxLength': (validators.positive_integer, False),
        'MinLength': (validators.positive_integer, False),
        'MaxValue': (validators.integer, False),
        'MinValue': (validators.integer, False),
        'Description': (basestring, False),
        'ConstraintDescription': (basestring, False),
    }

    def validate_title(self):
        if len(self.title) > PARAMETER_TITLE_MAX:
            raise ValueError("Parameter title can be no longer than "
                             "%d characters" % PARAMETER_TITLE_MAX)
        super(Parameter, self).validate_title()

    def validate(self):
        if self.properties['Type'] != 'String':
            for p in self.STRING_PROPERTIES:
                if p in self.properties:
                    raise ValueError("%s can only be used with parameters of "
                                     "the String type." % p)
        if self.properties['Type'] != 'Number':
            for p in self.NUMBER_PROPERTIES:
                if p in self.properties:
                    raise ValueError("%s can only be used with parameters of "
                                     "the Number type." % p)
