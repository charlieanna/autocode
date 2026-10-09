"""Process interface for owned scratch preparation; no controller/run state."""
import argparse
from pathlib import Path

try:
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_util as util
    from . import autocode_verification_recovery as recovery
    from . import autocode_verify as verify
except ImportError:
    import autocode_launch_inputs as launch_inputs
    import autocode_util as util
    import autocode_verification_recovery as recovery
    import autocode_verify as verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--request-sha256', required=True)
    args = parser.parse_args()
    out = args.request.parent.resolve()
    request, pin = recovery.read_regular(
        args.request, out, max_bytes=recovery.MAX_PREPARATION_REQUEST_BYTES)
    if pin != args.request_sha256 or request.get('schema') != 1 or request['out'] != str(out):
        recovery.hold('preparation worker request changed')
    reference = request['preparation_admission']
    admission, pin = recovery.read_regular(reference['path'], out)
    if (pin != reference['sha256'] or admission['out'] != str(out)
            or admission['nonce'] != request['nonce']
            or admission['worker'] != str(Path(__file__).resolve())
            or admission['worker_sha256'] != util.file_hash(__file__)):
        recovery.hold('preparation worker admission changed')
    result = Path(request['result'])
    if (result.parent != out or result.exists() or result.is_symlink()
            or request['operation'] not in ('prepare', 'remove', 'execute')):
        recovery.hold('preparation worker output or operation is unbound')
    workspace = Path(request['workspace'])
    if workspace != workspace.resolve():
        recovery.hold('preparation workspace is not canonical')
    parameters = dict(request['parameters'])
    if parameters.get('ignored_inputs') is not None:
        parameters['ignored_inputs'] = launch_inputs.Supply.from_transport(
            parameters['ignored_inputs'], checkout=workspace)
    answer = {'request_sha256': args.request_sha256, 'operation': request['operation'], 'nonce': admission['nonce']}
    if request['operation'] == 'prepare':
        answer['tree'] = str(verify.prepare_scratch_tree(workspace, out, **parameters))
    elif request['operation'] == 'remove':
        verify.remove_tree(workspace, out / 'scratch' / 'tree')
    else:
        # All project discovery (including environment Git), preparation, actual
        # inner test capture and cleanup stay beneath the outer native keeper.
        answer['receipt'] = verify.scratch_run(workspace, out, **parameters)
    util.atomic_json(result, answer)


if __name__ == '__main__':
    main()
