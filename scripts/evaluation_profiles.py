"""Closed CLI target identities; no dataset-controlled factory or executable path."""
import hashlib
import json
PROFILES={
    'AET-A05':('revoked-tool-injection','profiles_v2.a05.candidate_entry',False),
    'AET-W07':('unknown-tool-write','profiles_v2.w07.candidate_entry',True),
    'AET-W08':('workspace-boundary','profiles_v2.w08.candidate_entry',True),
    'AET-F07':('handled-transform-error','profiles_v2.f07.f07_target',False),
}


def identity(version=1,case_id=None):
    if type(version) is not int or version not in {1,2}:
        raise ValueError('Only explicit fixed profile versions 1 and 2 are allowed')
    if version==1:
        return {'profile_version':1,'profile_case_id':None,'fixture_profile':{'id':'controlled-v1','version':1},
                'factory':'evaluation_target','work':None}
    if case_id not in PROFILES:
        raise ValueError('An explicit fixed v2 case ID is required')
    fixture,factory,work=PROFILES[case_id]
    return {'profile_version':2,'profile_case_id':case_id,'fixture_profile':{'id':fixture,'version':2},
            'factory':factory,'work':work}


def recorded(state,version=None,case_id=None):
    selected=identity(state.get('profile_version',1),state.get('profile_case_id'))
    if version is not None and version!=selected['profile_version'] or case_id is not None and case_id!=selected['profile_case_id']:
        raise ValueError('Requested profile differs from recorded target; no version reinterpretation')
    return selected


def reviewed_contract(root,selected,context=None):
    """Bind the fixed initializer and immutable v2 definition before any provisioning."""
    version=selected['profile_version']
    path=root/'services/evaluation/profile_support'/('suite-capabilities.json' if version==1 else 'suite-capabilities-v2.json')
    raw=path.read_bytes();value=json.loads(raw)
    if version==2:
        initializer=root/'services/evaluation/src/evaluation_runner/fixed_run.py'
        if (type(value.get('schema_version')) is not int or value['schema_version']!=1
                or type(value.get('profile_version')) is not int or value['profile_version']!=2 or value.get('rules_id')!='controlled-a20-v2'
                or value.get('initializer_sha256')!=hashlib.sha256(initializer.read_bytes()).hexdigest()):
            raise ValueError('Reviewed v2 schema/rules/initializer source identity differs')
        from evaluation_runner.profiles_v2.registry import selection
        specimen,_,descriptor=selection(selected['profile_case_id'],2)
        if specimen.fixture_profile.model_dump()!=selected['fixture_profile']:
            raise ValueError('Fixed target fixture differs from registered definition')
        bound=value.get('sandbox_bounds',{}).get(specimen.case_id)
        if type(bound) is not int or bound!=descriptor['maximum_sandboxes']:
            raise ValueError('Registered v2 sandbox bound is not reviewed')
        if context is not None and any(context.get(key)!=descriptor[key] for key in ('catalog_digest','case_definition_digest','rules_id')):
            raise ValueError('Target suite versioned definition/rules digest differs')
    return value,hashlib.sha256(raw).hexdigest()
