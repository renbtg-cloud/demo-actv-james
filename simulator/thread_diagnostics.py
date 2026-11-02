#!/usr/bin/env python3
"""Bounded, auditable JDK 21 thread/JFR diagnostics for owned test JVMs.

Thread.dump_to_file(JSON) does not report a reliable RUNNABLE/BLOCKED/etc state for
all virtual threads. State counts here refer ONLY to the platform-thread text dump.
"""
import collections
import json
import os
import pathlib
import re
import subprocess
import time

STATES=('RUNNABLE','BLOCKED','WAITING','TIMED_WAITING','NEW','TERMINATED')

def invoke_jcmd(pid,*args,timeout=8):
    return subprocess.run(['jcmd',str(int(pid)),*map(str,args)],capture_output=True,text=True,timeout=timeout)

def parse_json_dump(document):
    payload=document.get('threadDump',{})
    groups=payload.get('threadContainers',[])
    total=0; stacks=collections.Counter(); groups_counter={}; parked=0
    for group in groups:
        threads=group.get('threads',[])
        groups_counter[group.get('container','unknown')]=len(threads)
        for th in threads:
            total+=1
            stack=th.get('stack',[])
            if stack:
                frame=next((v for v in stack if 'java.lang.Thread.run' not in v and 'LockSupport.' not in v),stack[0])
                stacks[frame]+=1
                if any('Unsafe.park' in s or 'LockSupport.park' in s for s in stack):parked+=1
    return {'all_listed_threads':total,'containers':groups_counter,'sampled_park_stacks':parked,
            'top_frames':stacks.most_common(12),
            'note':'No general thread-state field in JDK 21 JSON dump; virtual-thread parked counts are stack heuristics, not scheduler/carrier saturation.'}

def parse_platform_dump(text):
    counts=collections.Counter(re.findall(r'java\.lang\.Thread\.State:\s*([A-Z_]+)',text))
    return {'platform_thread_states':{key:counts[key] for key in STATES if counts[key]},
            'platform_threads_with_state':sum(counts.values()),
            'deadlock_reported': bool(re.search(r'Found (?:one|\d+) Java-level deadlock',text,re.I)),
            'monitor_ownership_samples':len(re.findall(r'- locked <0x',text))}

def snapshot(pid,folder,label,include_platform=True,timeout=8):
    folder=pathlib.Path(folder);folder.mkdir(parents=True,exist_ok=True)
    prefix=folder/(f'{label}-pid{pid}')
    summary={'pid':int(pid),'time_epoch_ms':int(time.time()*1000),'errors':[]}
    json_file=pathlib.Path(str(prefix)+'-threads.json')
    try:
        result=invoke_jcmd(pid,'Thread.dump_to_file','-format=json',json_file,timeout=timeout)
        if result.returncode or not json_file.is_file():summary['errors'].append('JSON dump failed: '+result.stderr[:300]+' '+result.stdout[:300])
        else:summary['json']=parse_json_dump(json.loads(json_file.read_text()))
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:summary['errors'].append(f'JSON dump: {type(exc).__name__}: {exc}')
    if include_platform:
        try:
            result=invoke_jcmd(pid,'Thread.print','-l',timeout=timeout)
            if result.returncode:summary['errors'].append('platform dump failed: '+result.stderr[:300])
            else:
                pathlib.Path(str(prefix)+'-platform.txt').write_text(result.stdout)
                summary['platform']=parse_platform_dump(result.stdout)
        except (OSError,subprocess.TimeoutExpired) as exc:summary['errors'].append(f'platform dump: {type(exc).__name__}: {exc}')
    pathlib.Path(str(prefix)+'-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

EVENTS=('jdk.VirtualThreadPinned','jdk.JavaMonitorEnter','jdk.ThreadPark','jdk.GarbageCollection','jdk.ExecutionSample','jdk.JavaThreadStatistics')

def jfr_configure_focus(filepath):
    """Enable stack traces and low thresholds for bounded diagnostic recording."""
    try:
        p=subprocess.run(['jfr','configure','--input','profile.jfc','--output',str(filepath),
            'jdk.VirtualThreadPinned#enabled=true',
            'jdk.VirtualThreadPinned#threshold=0ms',
            'jdk.VirtualThreadPinned#stackTrace=true',
            'jdk.JavaMonitorEnter#enabled=true',
            'jdk.JavaMonitorEnter#threshold=5ms',
            'jdk.JavaMonitorEnter#stackTrace=true',
            'jdk.ThreadPark#enabled=true',
            'jdk.ThreadPark#threshold=10ms'],capture_output=True,text=True,timeout=8)
        return {'ok':p.returncode==0 and pathlib.Path(filepath).is_file(),
                'message':(p.stdout+' '+p.stderr)[-300:]}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'ok':False,'error':str(exc)}

def jfr_start(pid,recording_name,settings_path=None):
    try:
        p=invoke_jcmd(pid,'JFR.start',f'name={recording_name}',f'settings={settings_path or "profile"}','disk=true','maxsize=20m',timeout=10)
        return {'ok':p.returncode==0,'output':(p.stdout+' '+p.stderr)[:600]}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'ok':False,'error':str(exc)}

def jfr_stop(pid,recording_name,filepath):
    try:
        p=invoke_jcmd(pid,'JFR.stop',f'name={recording_name}',f'filename={filepath}',timeout=12)
        return {'ok':p.returncode==0,'output':(p.stdout+' '+p.stderr)[:600]}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'ok':False,'error':str(exc)}

def jfr_events(path):
    """Aggregate selected JFR events; zero is a capture count, NOT proof of absence."""
    try:
        p=subprocess.run(['jfr','print','--json','--events',','.join(EVENTS),str(path)],
                         capture_output=True,text=True,timeout=15)
        if p.returncode:return {'error':p.stderr[-400:]}
        data=json.loads(p.stdout)
        counts=collections.Counter()
        pinned_durations_ms=[];pinned_trace_samples=[];monitor_traces=collections.Counter()
        for event in data.get('recording',{}).get('events',[]):
            typ=event.get('type','unknown');counts[typ]+=1
            v=event.get('values',{})
            trace=v.get('stackTrace') or {}
            frames=[]
            for item in trace.get('frames',[]):
                m=item.get('method') or {};owner=(m.get('type') or {}).get('name') or '?'
                frames.append(owner.replace('/','.')+'.'+(m.get('name') or '?'))
            if typ=='jdk.VirtualThreadPinned':
                duration=v.get('duration','')
                match=re.fullmatch(r'PT([\d.]+)S',str(duration))
                if match:pinned_durations_ms.append(float(match.group(1))*1000)
                pinned_trace_samples.append({'duration':duration,'duration_ms':round(float(match.group(1))*1000,3) if match else None,
                    'frames':frames[:20], 'truncated_or_missing':bool(trace.get('truncated')) or not frames})
            if typ=='jdk.JavaMonitorEnter' and frames:
                monitor_traces[frames[0]]+=1
        return {'counts':dict(counts),'requested_events':list(EVENTS),
                'virtual_thread_pinned_duration_total_ms':round(sum(pinned_durations_ms),3),
                'virtual_thread_pinned_duration_max_ms':round(max(pinned_durations_ms),3) if pinned_durations_ms else None,
                'virtual_thread_pinned_duration_buckets':{
                    'lt_1ms':sum(x<1 for x in pinned_durations_ms),
                    '1_to_lt_10ms':sum(1<=x<10 for x in pinned_durations_ms),
                    '10_to_lt_100ms':sum(10<=x<100 for x in pinned_durations_ms),
                    '100ms_and_more':sum(x>=100 for x in pinned_durations_ms)},
                'virtual_thread_pinned_stack_samples':sorted(pinned_trace_samples,key=lambda s:s.get('duration_ms') or 0,reverse=True)[:5],
                'monitor_enter_top_stack_frames':monitor_traces.most_common(8),
                'note':'Profile recording may not enable every event; count 0 does not rule out pinning/contention. Pinned stacks may be shallow and cannot always identify the lock or application frame. No inference of exact root cause from counts alone.'}
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:return {'error':f'{type(exc).__name__}: {exc}'}

def proc_info(pid):
    try:
        p=pathlib.Path('/proc')/str(int(pid))
        st=(p/'stat').read_text(); parts=st[st.rfind(')')+2:].split()
        ticks=os.sysconf(os.sysconf_names['SC_CLK_TCK'])
        # fields: (3) state, (4) ppid ... (14) utime (15) stime; zero-based offsets 11,12
        return {'pid':pid,'ppid':int(parts[1]),'cpu_s':(int(parts[11])+int(parts[12]))/ticks,
                'rss_bytes':int((p/'status').read_text().split('VmRSS:')[1].split('kB')[0])*1024,
                'os_threads':len(list((p/'task').iterdir()))}
    except (OSError,IndexError,ValueError):return None

def descendant_java_pids(coordinator):
    """Direct JVM agents spawned by the existing Python run.py coordinator."""
    found=[]
    for p in pathlib.Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            info=proc_info(int(p.name))
            if info and info['ppid']==coordinator and b'demo.simulator.LoadAgent' in (p/'cmdline').read_bytes():found.append(info)
        except (OSError,ValueError):pass
    return found

def cgroup_info():
    root=pathlib.Path('/sys/fs/cgroup')
    data={}
    for file in ('cpu.stat','memory.current','memory.events'):
        try:
            raw=(root/file).read_text().strip()
            if file=='memory.current':data['memory_bytes']=int(raw)
            else:data[file.replace('.','_')]={p[0]:int(p[1]) for s in raw.splitlines() if len(p:=s.split())==2}
        except (OSError,ValueError):pass
    return data
