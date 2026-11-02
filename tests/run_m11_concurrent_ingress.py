#!/usr/bin/env python3
"""Exercise the *current* integrated Java service under multi-JVM ingress load.

Fresh database schema, quorum queue and SQLite provider. No shared infrastructure
shutdown. Lab tokens remain only in a private temporary file, never in test reports.
"""
import json, os, pathlib, re, signal, socket, subprocess, sys, tempfile, time, uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]
SIM=ROOT/'simulator'/'run.py'

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    output=ROOT/'simulator'/'results'/('m11-'+uuid.uuid4().hex[:8]);output.mkdir(parents=True)
    api=free_port();provider=free_port()
    private=pathlib.Path(tempfile.mkdtemp(prefix='deroyque-m11-private-',dir='/mnt/data'))
    logfile=private/'demo.log'
    env={**os.environ,'DEROYQUE_API_PORT':str(api),'DEROYQUE_PROVIDER_PORT':str(provider),
         'APP_LAB_METRICS_ENABLED':'0'}
    demo=None;credential=None
    try:
        with logfile.open('w') as log:
            demo=subprocess.Popen(['python3','-u',str(ROOT/'scripts/dev_demo.py')],cwd=ROOT,env=env,
                       stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for i in range(450):
            if demo.poll() is not None:raise RuntimeError('demo exited early, see private log')
            message=logfile.read_text(errors='replace')
            match=re.search(r'Credential file \(chmod 600\): (\S+)',message)
            if match:
                credential=pathlib.Path(match.group(1));break
            time.sleep(.1)
        else:raise RuntimeError('no demo credential file')
        details=dict(line.split('=',1) for line in credential.read_text().splitlines())
        runenv={**os.environ,'DEROYQUE_SIM_READ_TOKEN':details['READ_TOKEN'],
                'DEROYQUE_SIM_WRITE_TOKEN':details['WRITE_TOKEN']}
        subprocess.run([str(ROOT/'simulator'/'build.sh')],check=True,stdout=subprocess.DEVNULL)
        runid='m11-'+uuid.uuid4().hex[:9]
        cmd=['python3','-u',str(SIM),'--url',f'http://127.0.0.1:{api}',
             '--auto-jvms','--auto-max-jvms','20','--concurrency','6','--requests','480',
             '--keys','120','--conflict-percent','25','--rate','110',
             '--heap-min-mb','16','--heap-max-mb','32',
             '--start-delay-ms','8000','--verify-state','--await-done-seconds','90',
             '--run-id',runid,'--output-dir',str(output/'workload')]
        with (output/'workload-console.log').open('w') as out:
            result=subprocess.run(cmd,cwd=ROOT,env=runenv,stdout=out,stderr=subprocess.STDOUT,timeout=180)
        summary=json.loads((output/'workload'/'summary.json').read_text())
        observed=summary['observed'];plan=summary['plan']
        compact={'outcome':summary['outcome'],'jvms_requested_auto':plan['jvms'],
            'peak_jvms_with_inflight_http':observed['peak_jvms_with_inflight_http'],
            'peak_http_inflight':observed['peak_inflight_http'],
            'requests':observed['records'],'unexpected_http_or_transport':observed['unexpected_http_or_transport'],
            'violations':len(summary['violations']),
            'harness_errors':len(summary['harness_errors']),
            'state_verification_errors':len(summary['state_verification_errors']),
            'verified_states':observed['verified_states'],
            'throughput_post_per_second':observed['post_attempts_per_second'],
            'p95_latency_ms':observed['latency_ms']['95'],
            'p95_schedule_lag_ms':observed['schedule_lag_ms']['95'],
            'script_returncode':result.returncode,
            'note':'All workers/producer JVMs reside on this Linux; this is not a multi-host performance benchmark.'}
        (output/'m11-load-result.json').write_text(json.dumps(compact,indent=2)+'\n')
        print(json.dumps(compact,indent=2),flush=True)
        return result.returncode
    finally:
        if demo is not None and demo.poll() is None:
            # The demo supervises its child processes; SIGINT invokes its finally/cleanup.
            os.kill(demo.pid,signal.SIGINT)
            try:demo.wait(timeout=12)
            except subprocess.TimeoutExpired:
                demo.terminate()
                try:demo.wait(timeout=5)
                except subprocess.TimeoutExpired:demo.kill();demo.wait()
        if credential is not None and credential.exists():credential.unlink()
        print('private_demo_log_not_distributed='+str(logfile),flush=True)

if __name__=='__main__':sys.exit(main())
