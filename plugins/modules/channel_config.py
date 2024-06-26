#!/usr/bin/python
#
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
import shutil
import subprocess
import urllib.parse
from subprocess import CalledProcessError

from ansible.module_utils._text import to_native
from ansible.module_utils.basic import _load_params, env_fallback

from pathlib import Path

from ..module_utils.dict_utils import diff_dicts
from ..module_utils.fabric_utils import get_fabric_cfg_path
from ..module_utils.file_utils import get_temp_file
from ..module_utils.module import BlockchainModule
from ..module_utils.msp_utils import convert_identity_to_msp_path
from ..module_utils.ordering_services import OrderingService
from ..module_utils.proto_utils import json_to_proto, proto_to_json
from ..module_utils.utils import (get_console, get_identity_by_module,
                                  get_ordering_service_by_module,
                                  get_ordering_service_nodes_by_module,
                                  get_organizations_by_module,
                                  resolve_identity)

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
---
module: channel_config
short_description: Manage the configuration for a Hyperledger Fabric channel
description:
    - Fetch and update the configuration for a Hyperledger Fabric channel.
    - This module works with the IBM Support for Hyperledger Fabric software or the Hyperledger Fabric
      Open Source Stack running in a Red Hat OpenShift or Kubernetes cluster.
author: Simon Stone (@sstone1)
options:
    api_endpoint:
        description:
            - The URL for the Fabric operations console.
        type: str
        required: true
    api_authtype:
        description:
            - C(basic) - Authenticate to the Fabric operations console using basic authentication.
              You must provide both a valid API key using I(api_key) and API secret using I(api_secret).
        type: str
        required: true
    api_key:
        description:
            - The API key for the Fabric operations console.
        type: str
        required: true
    api_secret:
        description:
            - The API secret for the Fabric operations console.
            - Only required when I(api_authtype) is C(basic).
        type: str
    api_timeout:
        description:
            - The timeout, in seconds, to use when interacting with the Fabric operations console.
        type: int
        default: 60
    operation:
        description:
            - C(create) - Create a channel configuration update transaction for a new channel.
            - C(fetch) - Fetch the current channel configuration to the specified I(path).
            - C(compute_update) - Compute a channel configuration update transaction using
              the original configuration at I(origin) and the updated configuration at
              I(updated).
            - C(sign_update) - Sign a channel configuration update transaction.
            - C(apply_update) - Apply a channel configuration update transaction.
        type: str
        required: true
    ordering_service:
        description:
            - The ordering service to use to manage the channel.
            - You can pass a string, which is the cluster name of a ordering service registered
              with the Fabric operations console.
            - You can also pass a list, which must match the result format of one of the
              M(ordering_service_info) or M(ordering_service) modules.
            - Only required when I(operation) is C(fetch) or C(apply_update).
            - Cannot be specified with I(ordering_service_nodes).
        type: raw
    ordering_service_nodes:
        description:
            - The ordering service nodes to use to manage the channel.
            - You can pass strings, which are the names of ordering service nodes that are
              registered with the Fabric operations console.
            - You can also pass a dict, which must match the result format of one
              of the M(ordering_service_node_info) or M(ordering_service_node) modules.
            - Only required when I(operation) is C(fetch) or C(apply_update).
            - Cannot be specified with I(ordering_service).
            - If specified when I(operation) is C(create), then the specified ordering service nodes
              are used as the consenters for the channel. This is useful when you want to use a subset
              of nodes in an ordering service; for example, when you only want to use three ordering
              service nodes from a five node ordering service.
        type: raw
    identity:
        description:
            - The identity to use when interacting with the ordering service or for signing
              channel configuration update transactions.
            - You can pass a string, which is the path to the JSON file where the enrolled
              identity is stored.
            - You can also pass a dict, which must match the result format of one of the
              M(enrolled_identity_info) or M(enrolled_identity) modules.
            - Only required when I(operation) is C(fetch), C(sign_update), or C(apply_update).
        type: raw
    msp_id:
        description:
            - The MSP ID to use for interacting with the ordering service or for signing
              channel configuration update transactions.
            - Only required when I(operation) is C(fetch), C(sign), or C(apply_update).
        type: str
    hsm:
        description:
            - "The PKCS #11 compliant HSM configuration to use for digital signatures."
            - Only required if the identity specified in I(identity) was enrolled using an HSM,
              and when I(operation) is C(fetch), C(sign), or C(apply_update).
        type: dict
        suboptions:
            pkcs11library:
                description:
                    - "The PKCS #11 library that should be used for digital signatures."
                type: str
            label:
                description:
                    - The HSM label that should be used for digital signatures.
                type: str
            pin:
                description:
                    - The HSM pin that should be used for digital signatures.
                type: str
    name:
        description:
            - The name of the channel.
        type: str
        required: true
    path:
        description:
            - The path to the file where the channel configuration or the channel configuration
              update transaction will be stored.
        type: str
        required: true
    original:
        description:
            - The path to the file where the original channel configuration is stored.
            - Only required when I(operation) is C(compute_update).
        type: str
    updated:
        description:
            - The path to the file where the updated channel configuration is stored.
            - Only required when I(operation) is C(compute_update).
        type: str
    organizations:
        description:
            - The list of organizations to add as members in the new channel.
            - The organizations must all be members of the consortium.
            - You can pass strings, which are the names of organizations that are
              registered with the Fabric operations console.
            - You can also pass a dict, which must match the result format of one
              of the M(organization_info) or M(organization) modules.
            - Only required when I(operation) is C(create).
        type: list
        elements: raw
    policies:
        description:
            - The set of policies to add to the new channel. The keys are the policy
              names, and the values are the policies.
            - You can pass strings, which are paths to JSON files containing policies
              in the Hyperledger Fabric format (common.Policy).
            - You can also pass a dict, which must correspond to a parsed policy in the
              Hyperledger Fabric format (common.Policy).
            - You must provide at least an Admins, Writers, and Readers policy.
            - Only required when I(operation) is C(create).
        type: dict
    acls:
        description:
            - The set of ACLs to add to the new channel. The keys are the ACL names, and
              the values are the name of the policy used by the ACL.
        type: dict
    capabilities:
        description:
            - The capability levels for the new channel.
        type: dict
        suboptions:
            application:
                description:
                    - The application capability level for the new channel.
                    - The value must be a valid application capability level supported by Hyperledger Fabric,
                      and all peers that will join the new channel must support this application capability level.
                    - Example application capability levels include C(V1_4_2) and C(V2_0).
                type: str
                default: V1_4_2
            channel:
                description:
                    - The channel capability level.
                    - The value must be a valid channel capability level supported by Hyperledger Fabric,
                      and all peers and ordering service nodes in the new channel must support this channel
                      capability level.
                    - Example channel capability levels include C(V1_4_3) and C(V2_0).
                type: str
            orderer:
                description:
                    - The orderer capability level for the new channel.
                    - The value must be a valid orderer capability level supported by Hyperledger Fabric,
                      and all ordering service nodes in the new channel must support this orderer capability
                      level.
                    - Example orderer capability levels include C(V1_4_2) and C(V2_0).
                type: str
    parameters:
        description:
            - The parameters for the new channel.
        type: dict
        suboptions:
            batch_size:
                description:
                    - The batch size parameters for the channel.
                type: dict
                suboptions:
                    max_message_count:
                        description:
                            - The maximum number of messages that should be present in a block for the channel.
                        type: int
                    absolute_max_bytes:
                        description:
                            - The total size of all the messages in a block for the channel must not exceed this value.
                        type: int
                    preferred_max_bytes:
                        description:
                            - The total size of all the messages in a block for the channel should not exceed this value.
                        type: int
            batch_timeout:
                description:
                    - The maximum time to wait before cutting a new block for the channel.
                    - Example values include I(500ms), I(5m), or I(24h).
                type: str
    tls_handshake_time_shift:
        type: str
        description:
            - The amount of time to shift backwards for certificate expiration checks during TLS handshakes with the ordering service endpoint.
            - Only use this option if the ordering service TLS certificates have expired.
            - The value must be a duration, for example I(30m), I(24h), or I(6h30m).
notes: []
requirements: []
'''

EXAMPLES = '''
- name: Create the configuration for a new channel
  hyperledger.fabric_ansible_collection.channel_config:
    api_endpoint: https://console.example.org:32000
    api_authtype: basic
    api_key: xxxxxxxx
    api_secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    operation: create
    name: mychannel
    path: channel_config_update.bin
    organizations:
      - Org1
    policies:
      Admins: admins-policy.json
      Readers: readers-policy.json
      Writers: writers-policy.json

- name: Fetch the channel configuration
  hyperledger.fabric_ansible_collection.channel_config:
    api_endpoint: https://console.example.org:32000
    api_authtype: basic
    api_key: xxxxxxxx
    api_secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ordering_service: Ordering Service
    identity: Org1 Admin.json
    msp_id: Org1MSP
    operation: fetch
    name: mychannel
    path: channel_config.bin

- name: Compute the configuration update for the channel
  hyperledger.fabric_ansible_collection.channel_config:
    operation: compute_update
    name: mychannel
    original: original_channel_config.bin
    updated: updated_channel_config.bin
    path: channel_config_update.bin

- name: Sign the configuration update for the channel
  hyperledger.fabric_ansible_collection.channel_config:
    operation: sign_update
    identity: Org1 Admin.json
    msp_id: Org1MSP
    name: mychannel
    path: channel_config_update.bin

- name: Apply the configuration update for the channel
  hyperledger.fabric_ansible_collection.channel_config:
    api_endpoint: https://console.example.org:32000
    api_authtype: basic
    api_key: xxxxxxxx
    api_secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ordering_service: Ordering Service
    identity: Org1 Admin.json
    msp_id: Org1MSP
    operation: apply_update
    name: mychannel
    path: channel_config_update.bin
'''

RETURN = '''
---
path:
    description:
        - The path to the file where the channel configuration or the channel configuration
          update transaction is stored.
    type: str
    returned: always
'''


def create(module):

    # Log in to the console.
    console = get_console(module)

    # Get the organizations.
    organizations = get_organizations_by_module(console, module)

    # Get the policies.
    policies = module.params['policies']
    actual_policies = dict()
    for policyName, policy in policies.items():
        if isinstance(policy, str):
            with open(policy, 'r') as file:
                actual_policies[policyName] = json.load(file)
        elif isinstance(policy, dict):
            actual_policies[policyName] = policy
        else:
            raise Exception(f'The policy {policyName} is invalid')

    # Build the config update for a new channel.
    name = module.params['name']
    application_capability = module.params['capabilities']['application']
    config_update_json = dict(
        channel_id=name,
        read_set=dict(
            groups=dict(
                Application=dict(
                    groups=dict()
                )
            ),
            values=dict(
                Consortium=dict(
                    value=dict(
                        name='SampleConsortium'
                    )
                )
            )
        ),
        write_set=dict(
            groups=dict(
                Application=dict(
                    groups=dict(),
                    mod_policy='Admins',
                    policies=dict(),
                    values=dict(
                        Capabilities=dict(
                            mod_policy='Admins',
                            value=dict(
                                capabilities={
                                    application_capability: {}
                                }
                            )
                        )
                    ),
                    version=1
                )
            ),
            values=dict(
                Consortium=dict(
                    value=dict(
                        name='SampleConsortium'
                    )
                )
            )
        )
    )

    # Add the organizations to the config update.
    for organization in organizations:
        config_update_json['read_set']['groups']['Application']['groups'][organization.msp_id] = dict()
        config_update_json['write_set']['groups']['Application']['groups'][organization.msp_id] = dict()

    # Add the policies to the config update.
    for policyName, policy in actual_policies.items():
        config_update_json['write_set']['groups']['Application']['policies'][policyName] = dict(
            mod_policy='Admins',
            policy=policy
        )

    # Add the channel and orderer capabilities to the config update.
    capabilities = module.params['capabilities']
    if capabilities:

        # Handle the channel capability.
        channel = capabilities['channel']
        if channel:
            config_update_json['read_set']['values'].setdefault('Capabilities', dict())
            config_update_json['write_set']['values']['Capabilities'] = dict(
                mod_policy='Admins',
                value=dict(
                    capabilities={
                        channel: {}
                    }
                ),
                version=1
            )

        # Handle the orderer capability.
        orderer = capabilities['orderer']
        if orderer:
            config_update_json['read_set']['groups'].setdefault('Orderer', dict()).setdefault('values', dict()).setdefault('Capabilities', dict())
            orderer_group = config_update_json['write_set']['groups'].setdefault('Orderer', dict())
            orderer_values = orderer_group.setdefault('values', dict())
            orderer_values['Capabilities'] = dict(
                mod_policy='Admins',
                value=dict(
                    capabilities={
                        orderer: {}
                    }
                ),
                version=1
            )

    # Add the parameters to the config update.
    parameters = module.params['parameters']
    if parameters:

        # Handle the batch size.
        batch_size = parameters['batch_size']
        if batch_size:
            config_update_json['read_set']['groups'].setdefault('Orderer', dict()).setdefault('values', dict()).setdefault('BatchSize', dict())
            orderer_group = config_update_json['write_set']['groups'].setdefault('Orderer', dict())
            orderer_values = orderer_group.setdefault('values', dict())
            orderer_batch_size_value = orderer_values.setdefault('BatchSize', dict(
                mod_policy='Admins',
                value=dict(),
                version=1
            ))
            for key in batch_size:
                if batch_size[key]:
                    orderer_batch_size_value['value'][key] = batch_size[key]

        # Handle the batch timeout.
        batch_timeout = parameters['batch_timeout']
        if batch_timeout:
            config_update_json['read_set']['groups'].setdefault('Orderer', dict()).setdefault('values', dict()).setdefault('BatchTimeout', dict())
            orderer_group = config_update_json['write_set']['groups'].setdefault('Orderer', dict())
            orderer_values = orderer_group.setdefault('values', dict())
            orderer_values['BatchTimeout'] = dict(
                mod_policy='Admins',
                value=dict(
                    timeout=batch_timeout
                ),
                version=1
            )

    # Add the ACLs to the config update.
    acls = module.params['acls']
    if acls:
        application_group = config_update_json['write_set']['groups']['Application']
        application_values = application_group.setdefault('values', dict())
        acls_value = application_values.setdefault('ACLs', dict(
            mod_policy='Admins',
            value=dict(
                acls=dict()
            ),
            version=0
        ))
        for acl_name, acl_policy in acls.items():
            acls_value['value']['acls'][acl_name] = dict(policy_ref=acl_policy)

    # Handle the ordering service nodes.
    if module.params['ordering_service_nodes'] is not None:

        # Extract the ordering service nodes.
        ordering_service_nodes = get_ordering_service_nodes_by_module(console, module)

        # Build the list of consenters.
        consenters = list()
        for ordering_service_node in ordering_service_nodes:
            parsed_api_url = urllib.parse.urlparse(ordering_service_node.api_url)
            host = parsed_api_url.hostname
            port = parsed_api_url.port or 443
            client_tls_cert = ordering_service_node.client_tls_cert or ordering_service_node.tls_cert
            server_tls_cert = ordering_service_node.server_tls_cert or ordering_service_node.tls_cert
            consenters.append(dict(
                host=host,
                port=port,
                client_tls_cert=client_tls_cert,
                server_tls_cert=server_tls_cert,
            ))

        # Build the list of orderer addresses.
        orderer_addresses = set()
        for ordering_service_node in ordering_service_nodes:
            parsed_api_url = urllib.parse.urlparse(ordering_service_node.api_url)
            host = parsed_api_url.hostname
            port = parsed_api_url.port or 443
            orderer_addresses.add(f'{host}:{port}')

        # Update the configuration.
        config_update_json['read_set']['groups'].setdefault('Orderer', dict()).setdefault('values', dict()).setdefault('ConsensusType', dict())
        orderer_group = config_update_json['write_set']['groups'].setdefault('Orderer', dict())
        orderer_values = orderer_group.setdefault('values', dict())
        orderer_values['ConsensusType'] = dict(
            mod_policy='Admins',
            value=dict(
                type='etcdraft',
                metadata=dict(
                    consenters=consenters,
                    options=dict(
                        tick_interval='500ms',
                        election_tick=10,
                        heartbeat_tick=1,
                        max_inflight_blocks=5,
                        snapshot_interval_size=20971520
                    )
                )
            ),
            version=1
        )
        config_update_json['read_set']['values'].setdefault('OrdererAddresses', dict())
        config_update_json['write_set']['values']['OrdererAddresses'] = dict(
            mod_policy='/Channel/Orderer/Admins',
            value=dict(
                addresses=list(orderer_addresses)
            ),
            version=1
        )

    # Build the config envelope.
    config_update_envelope_json = dict(
        payload=dict(
            header=dict(
                channel_header=dict(
                    channel_id=name,
                    type=2
                )
            ),
            data=dict(
                config_update=config_update_json
            )
        )
    )
    config_update_envelope_proto = json_to_proto('common.Envelope', config_update_envelope_json)

    # Compare and copy if needed.
    path = module.params['path']
    if os.path.exists(path):
        changed = False
        try:
            with open(path, 'rb') as file:
                original_config_update_envelope_json = proto_to_json('common.Envelope', file.read())
            changed = diff_dicts(original_config_update_envelope_json, config_update_envelope_json)
        except Exception:
            changed = True
        if changed:
            with open(path, 'wb') as file:
                file.write(config_update_envelope_proto)
        module.exit_json(changed=changed, path=path)
    else:
        with open(path, 'wb') as file:
            file.write(config_update_envelope_proto)
        module.exit_json(changed=True, path=path)


def fetch(module):

    # Log in to the console.
    console = get_console(module)

    # Get the ordering service.
    ordering_service_specified = module.params['ordering_service'] is not None
    if ordering_service_specified:
        ordering_service = get_ordering_service_by_module(console, module)
    else:
        ordering_service_nodes = get_ordering_service_nodes_by_module(console, module)
        ordering_service = OrderingService(ordering_service_nodes)
    tls_handshake_time_shift = module.params['tls_handshake_time_shift']

    # Get the identity.
    identity = get_identity_by_module(module)
    msp_id = module.params['msp_id']
    hsm = module.params['hsm']
    identity = resolve_identity(console, module, identity, msp_id)

    # Get the channel and target path.
    name = module.params['name']
    path = module.params['path']

    # Create a temporary file to hold the block.
    block_proto_path = get_temp_file()
    try:

        # Fetch the block.
        with ordering_service.connect(module, identity, msp_id, hsm, tls_handshake_time_shift) as connection:
            connection.fetch(name, 'config', block_proto_path)

        # Convert it into JSON.
        with open(block_proto_path, 'rb') as file:
            block_json = proto_to_json('common.Block', file.read())

        # Extract the config.
        config_json = block_json['data']['data'][0]['payload']['data']['config']
        config_proto = json_to_proto('common.Config', config_json)

        # Compare and copy if needed.
        if os.path.exists(path):
            changed = False
            try:
                with open(path, 'rb') as file:
                    original_config_json = proto_to_json('common.Config', file.read())
                changed = diff_dicts(original_config_json, config_json)
            except Exception:
                changed = True
            if changed:
                with open(path, 'wb') as file:
                    file.write(config_proto)
            module.exit_json(changed=changed, path=path)
        else:
            with open(path, 'wb') as file:
                file.write(config_proto)
            module.exit_json(changed=True, path=path)

    # Ensure the temporary file is cleaned up.
    finally:
        os.remove(block_proto_path)


def compute_update(module):

    # Get the channel and target path.
    name = module.params['name']
    path = module.params['path']
    original = module.params['original']
    updated = module.params['updated']

    # Create a temporary file to hold the block.
    config_update_proto_path = get_temp_file()
    try:

        # Run the command to compute the update
        try:
            subprocess.run([
                'configtxlator', 'compute_update', f'--channel_id={name}', f'--original={original}', f'--updated={updated}', f'--output={config_update_proto_path}'
            ], text=True, close_fds=True, check=True, capture_output=True)
        except CalledProcessError as e:
            if e.stderr.find('no differences detected') != -1:
                if os.path.exists(path):
                    os.remove(path)
                    return module.exit_json(changed=True, path=None)
                else:
                    return module.exit_json(changed=False, path=None)
            raise

        # Convert it into JSON.
        with open(config_update_proto_path, 'rb') as file:
            config_update_json = proto_to_json('common.ConfigUpdate', file.read())

        # Build the config envelope.
        config_update_envelope_json = dict(
            payload=dict(
                header=dict(
                    channel_header=dict(
                        channel_id=name,
                        type=2
                    )
                ),
                data=dict(
                    config_update=config_update_json
                )
            )
        )
        config_update_envelope_proto = json_to_proto('common.Envelope', config_update_envelope_json)

        # Compare and copy if needed.
        if os.path.exists(path):
            changed = False
            try:
                with open(path, 'rb') as file:
                    original_config_update_envelope_json = proto_to_json('common.Envelope', file.read())
                changed = diff_dicts(original_config_update_envelope_json, config_update_envelope_json)
            except Exception:
                changed = True
            if changed:
                with open(path, 'wb') as file:
                    file.write(config_update_envelope_proto)
            module.exit_json(changed=changed, path=path)
        else:
            with open(path, 'wb') as file:
                file.write(config_update_envelope_proto)
            module.exit_json(changed=True, path=path)

    # Ensure the temporary file is cleaned up.
    finally:
        os.remove(config_update_proto_path)


def sign_update(module):

    # Get the channel and target path.
    path = module.params['path']

    # Get the identity and MSP ID.
    identity = get_identity_by_module(module)
    msp_id = module.params['msp_id']
    hsm = module.params['hsm']

    # HACK: we don't require the console for this operation, but the following
    # function call might require it.
    identity = resolve_identity(None, module, identity, msp_id)

    # Load in the existing config update file and see if we've already signed it.
    with open(path, 'rb') as file:
        config_update_envelope_json = proto_to_json('common.Envelope', file.read())
    signatures = config_update_envelope_json['payload']['data'].get('signatures', list())
    for signature in signatures:
        if msp_id == signature['signature_header']['creator']['mspid']:
            return module.exit_json(changed=False, path=path)

    # Need to sign it.
    msp_path = convert_identity_to_msp_path(identity)
    fabric_cfg_path = get_fabric_cfg_path()
    try:
        env = os.environ.copy()
        env['CORE_PEER_MSPCONFIGPATH'] = msp_path
        env['CORE_PEER_LOCALMSPID'] = msp_id
        env['FABRIC_CFG_PATH'] = fabric_cfg_path
        if hsm:
            env['CORE_PEER_BCCSP_DEFAULT'] = 'PKCS11'
            env['CORE_PEER_BCCSP_PKCS11_LIBRARY'] = hsm['pkcs11library']
            env['CORE_PEER_BCCSP_PKCS11_LABEL'] = hsm['label']
            env['CORE_PEER_BCCSP_PKCS11_PIN'] = hsm['pin']
            env['CORE_PEER_BCCSP_PKCS11_HASH'] = 'SHA2'
            env['CORE_PEER_BCCSP_PKCS11_SECURITY'] = '256'
            env['CORE_PEER_BCCSP_PKCS11_FILEKEYSTORE_KEYSTORE'] = os.path.join(msp_path, 'keystore')
        subprocess.run([
            'peer', 'channel', 'signconfigtx', '-f', path
        ], env=env, text=True, close_fds=True, check=True, capture_output=True)
        module.exit_json(changed=True, path=path)
    finally:
        shutil.rmtree(msp_path)
        shutil.rmtree(fabric_cfg_path)


def sign_update_organizations(module):

    # Get the channel and target path.
    path = module.params['path']

    organizations_dir_param = module.params['organizations_dir']

    organizations_dir = Path(organizations_dir_param).resolve()

    hsm = module.params['hsm']

    # Load in the existing config update file and see if we've already signed it.
    with open(path, 'rb') as file:
        config_update_envelope_json = proto_to_json('common.Envelope', file.read())
    signatures = config_update_envelope_json['payload']['data'].get('signatures', list())

    module.json_log({
        'msg': 'Organizations for signing the update',
        'Organizations': module.params['organizations']
    })

    for msp_id in module.params['organizations']:

        for signature in signatures:
            if msp_id == signature['signature_header']['creator']['mspid']:
                continue

        # Need to sign it.
        msp_path = os.path.join(organizations_dir, msp_id, "msp")
        fabric_cfg_path = get_fabric_cfg_path()

        module.json_log({
            'msg': 'Adding signature to change',
            'CORE_PEER_MSPCONFIGPATH': msp_path,
            'CORE_PEER_LOCALMSPID': msp_id,
            'FABRIC_CFG_PATH': fabric_cfg_path
        })

        try:
            env = os.environ.copy()
            env['CORE_PEER_MSPCONFIGPATH'] = msp_path
            env['CORE_PEER_LOCALMSPID'] = msp_id
            env['FABRIC_CFG_PATH'] = fabric_cfg_path
            if hsm:
                env['CORE_PEER_BCCSP_DEFAULT'] = 'PKCS11'
                env['CORE_PEER_BCCSP_PKCS11_LIBRARY'] = hsm['pkcs11library']
                env['CORE_PEER_BCCSP_PKCS11_LABEL'] = hsm['label']
                env['CORE_PEER_BCCSP_PKCS11_PIN'] = hsm['pin']
                env['CORE_PEER_BCCSP_PKCS11_HASH'] = 'SHA2'
                env['CORE_PEER_BCCSP_PKCS11_SECURITY'] = '256'
                env['CORE_PEER_BCCSP_PKCS11_FILEKEYSTORE_KEYSTORE'] = os.path.join(msp_path, 'keystore')
            subprocess.run([
                'peer', 'channel', 'signconfigtx', '-f', path
            ], env=env, text=True, close_fds=True, check=True, capture_output=True)

        finally:
            shutil.rmtree(fabric_cfg_path)

    module.exit_json(changed=True, path=path)


def apply_update(module):

    # Log in to the console.
    console = get_console(module)

    # Get the ordering service.
    ordering_service_specified = module.params['ordering_service'] is not None
    if ordering_service_specified:
        ordering_service = get_ordering_service_by_module(console, module)
    else:
        ordering_service_nodes = get_ordering_service_nodes_by_module(console, module)
        ordering_service = OrderingService(ordering_service_nodes)
    tls_handshake_time_shift = module.params['tls_handshake_time_shift']

    # Get the identity.
    identity = get_identity_by_module(module)
    msp_id = module.params['msp_id']
    hsm = module.params['hsm']
    identity = resolve_identity(console, module, identity, msp_id)

    # Get the channel and target path.
    name = module.params['name']
    path = module.params['path']

    # Update the channel.
    with ordering_service.connect(module, identity, msp_id, hsm, tls_handshake_time_shift) as connection:
        connection.update(name, path)
    module.exit_json(changed=True)


def main():

    # Create the module.
    argument_spec = dict(
        api_endpoint=dict(type='str'),
        api_authtype=dict(type='str', choices=['ibmcloud', 'basic']),
        api_key=dict(type='str', no_log=True),
        api_secret=dict(type='str', no_log=True),
        api_timeout=dict(type='int', default=60),
        api_token_endpoint=dict(type='str', default='https://iam.cloud.ibm.com/identity/token'),
        operation=dict(type='str', required=True, choices=['create', 'fetch', 'compute_update', 'sign_update', 'sign_update_organizations', 'apply_update']),
        ordering_service=dict(type='raw'),
        ordering_service_nodes=dict(type='list', elements='raw'),
        tls_handshake_time_shift=dict(type='str', fallback=(env_fallback, ['IBP_TLS_HANDSHAKE_TIME_SHIFT'])),   # TODO: Look into renaming this env variable
        identity=dict(type='raw'),
        msp_id=dict(type='str'),
        hsm=dict(type='dict', options=dict(
            pkcs11library=dict(type='str', required=True),
            label=dict(type='str', required=True, no_log=True),
            pin=dict(type='str', required=True, no_log=True)
        )),
        name=dict(type='str'),
        path=dict(type='str'),
        original=dict(type='str'),
        updated=dict(type='str'),
        organizations=dict(type='list', elements='raw'),
        organizations_dir=dict(type='str', default='organizations'),
        policies=dict(type='dict'),
        acls=dict(type='dict'),
        capabilities=dict(type='dict', default=dict(), options=dict(
            application=dict(type='str', default='V1_4_2'),
            channel=dict(type='str'),
            orderer=dict(type='str')
        )),
        parameters=dict(type='dict', default=dict(), options=dict(
            batch_size=dict(type='dict', options=dict(
                max_message_count=dict(type='int'),
                absolute_max_bytes=dict(type='int'),
                preferred_max_bytes=dict(type='int')
            )),
            batch_timeout=dict(type='str')
        ))
    )
    required_if = [
        ('api_authtype', 'basic', ['api_secret']),
        ('operation', 'create', ['api_endpoint', 'api_authtype', 'api_key', 'organizations', 'policies', 'name', 'path']),
        ('operation', 'fetch', ['api_endpoint', 'api_authtype', 'api_key', 'identity', 'msp_id', 'name', 'path']),
        ('operation', 'compute_update', ['name', 'path', 'original', 'updated']),
        ('operation', 'sign_update', ['identity', 'msp_id', 'name', 'path']),
        ('operation', 'sign_update_organizations', ['organizations', 'organizations_dir', 'name', 'path']),
        ('operation', 'apply_update', ['api_endpoint', 'api_authtype', 'api_key', 'identity', 'msp_id', 'name', 'path'])
    ]
    # Ansible doesn't allow us to say "require one of X and Y only if condition A is true",
    # so we need to handle this ourselves by seeing what was passed in.
    actual_params = _load_params()
    if actual_params.get('operation', None) in ['fetch', 'apply_update']:
        required_one_of = [
            ['ordering_service', 'ordering_service_nodes']
        ]
    else:
        required_one_of = []
    module = BlockchainModule(argument_spec=argument_spec, supports_check_mode=True, required_if=required_if, required_one_of=required_one_of)

    # Validate HSM requirements if HSM is specified.
    if module.params['hsm']:
        module.check_for_missing_hsm_libs()

    # Ensure all exceptions are caught.
    try:
        operation = module.params['operation']
        if operation == 'create':
            create(module)
        elif operation == 'fetch':
            fetch(module)
        elif operation == 'compute_update':
            compute_update(module)
        elif operation == 'sign_update':
            sign_update(module)
        elif operation == 'sign_update_organizations':
            sign_update_organizations(module)
        elif operation == 'apply_update':
            apply_update(module)
        else:
            raise Exception(f'Invalid operation {operation}')

    # Notify Ansible of the exception.
    except Exception as e:
        module.fail_json(msg=to_native(e))


if __name__ == '__main__':
    main()
