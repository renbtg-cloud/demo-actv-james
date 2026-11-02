package demo;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Immutable per-request provider response rules, NEVER inferred from 2xx/4xx alone. */
public final class P15Policy {
  public enum Evidence { ACTIVATED, REJECTED, ABSENT, INVALID }
  public enum Action { ADOPT_ACTIVATED, ADOPT_REJECTED, RETRY_AS_500, PAUSE_UNVERIFIED, RATE_LIMITED,
    PAUSE_PROTOCOL, CONTRACT_SUCCESS, CONTRACT_REJECTION }
  record Rule(int from,int to,Evidence evidence,Action action) {}
  public record Decision(Evidence evidence,Action action,String revision) {}
  final List<Rule> rules;
  final String spec,revision;
  P15Policy(String spec,List<Rule> rules){this.spec=spec;this.rules=List.copyOf(rules);this.revision=hash(spec);}
  public static String hash(String data){try {
    byte[] bytes=MessageDigest.getInstance("SHA-256").digest(data.getBytes(StandardCharsets.UTF_8));
    return HexFormat.of().formatHex(bytes);
  }catch(Exception e){throw new IllegalStateException(e);}}
  /** Format: first line STATUS_ONLY=YES|NO; second line CONTRACT=...; remaining lines start-end,EVIDENCE,ACTION.
   * All config validation is performed at startup, including ambiguous overlaps. */
  public static P15Policy parse(String spec) {
    String[] lines=spec.strip().replace("\r","").split("\n");
    if(lines.length<3 || !lines[0].matches("STATUS_ONLY=(YES|NO)") || !lines[1].startsWith("CONTRACT="))
      throw new IllegalArgumentException("config header");
    boolean statusOnly=lines[0].equals("STATUS_ONLY=YES");String contract=lines[1].substring(9).trim();
    if(statusOnly && contract.length()<12)throw new IllegalArgumentException("status-only requires written provider contract");
    List<Rule> result=new ArrayList<>();
    for(int i=2;i<lines.length;i++){
      String line=lines[i].trim();if(line.isBlank()||line.startsWith("#"))continue;
      String[] pieces=line.split(",",-1);if(pieces.length!=3)throw new IllegalArgumentException("bad rule: "+line);
      String[] range=pieces[0].split("-");int from,to;
      try{from=Integer.parseInt(range[0]);to=range.length==1?from:Integer.parseInt(range[1]);}
      catch(Exception e){throw new IllegalArgumentException("bad status range "+line);}
      if(range.length>2||from<200||to>599||from>to)throw new IllegalArgumentException("status outside 200..599");
      Evidence ev=Evidence.valueOf(pieces[1].trim());Action a=Action.valueOf(pieces[2].trim());
      if(ev==Evidence.INVALID)throw new IllegalArgumentException("invalid receipt must always fail closed");
      if(a==Action.PAUSE_PROTOCOL)throw new IllegalArgumentException("protocol anomaly action is reserved");
      if(a==Action.ADOPT_ACTIVATED && ev!=Evidence.ACTIVATED)throw new IllegalArgumentException("unverified activated");
      if(a==Action.ADOPT_REJECTED && ev!=Evidence.REJECTED)throw new IllegalArgumentException("unverified rejected");
      if((a==Action.CONTRACT_SUCCESS||a==Action.CONTRACT_REJECTION) && (!statusOnly||ev!=Evidence.ABSENT))throw new IllegalArgumentException("unapproved status-only action");
      for(Rule r:result)if(ev==r.evidence && from<=r.to && to>=r.from)throw new IllegalArgumentException("overlapping rules");
      result.add(new Rule(from,to,ev,a));
    }
    if(result.isEmpty())throw new IllegalArgumentException("no rules");
    return new P15Policy(spec,result);
  }
  public static final String DEFAULT = """
      STATUS_ONLY=NO
      CONTRACT=mock provider receipts require exact key and outcome
      200,ACTIVATED,ADOPT_ACTIVATED
      422,REJECTED,ADOPT_REJECTED
      429,ABSENT,RATE_LIMITED
      400,ABSENT,PAUSE_UNVERIFIED
      401,ABSENT,PAUSE_UNVERIFIED
      403,ABSENT,PAUSE_UNVERIFIED
      404,ABSENT,PAUSE_UNVERIFIED
      409,ABSENT,PAUSE_UNVERIFIED
      501,ABSENT,PAUSE_UNVERIFIED
      408,ABSENT,RETRY_AS_500
      """;
  public Decision decide(Integer status,String receipt,String key){
    Evidence evidence;
    if(receipt==null||receipt.isBlank())evidence=Evidence.ABSENT;
    else if(receipt.equals("MOCK:ACTIVATED:"+key))evidence=Evidence.ACTIVATED;
    else if(receipt.equals("MOCK:REJECTED:"+key))evidence=Evidence.REJECTED;
    else evidence=Evidence.INVALID;
    if(evidence==Evidence.INVALID)return new Decision(evidence,Action.PAUSE_PROTOCOL,revision);
    if(status==null||status<200||status>599)return new Decision(evidence,Action.RETRY_AS_500,revision);
    for(Rule r:rules)if(status>=r.from&&status<=r.to&&r.evidence==evidence)return new Decision(evidence,r.action,revision);
    return new Decision(evidence,Action.RETRY_AS_500,revision);
  }
}
