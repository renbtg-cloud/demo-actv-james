package demo;

import java.util.Map;
import java.util.Random;

/** Reproducible adversarial Unicode tests of the tiny core JSON parser.
 * Neither Spring nor an external broker is needed. Values are independently
 * constructed from Unicode code points, then represented two different ways.
 */
public final class StrictJsonUnicodePropertyTest {
  private static final long SEED=20261102L;
  private static final Random random=new Random(SEED);
  private static int total,failed;
  private static void check(boolean condition,String message){
    total++;
    if(!condition){failed++;System.err.println("FAIL "+failed+"/"+total+" "+message);}
  }
  private static String encoding(String input,boolean asciiOnly){
    StringBuilder result=new StringBuilder("\"");
    for(int i=0;i<input.length();i++){
      char c=input.charAt(i);
      if(c=='"')result.append("\\\"");
      else if(c=='\\')result.append("\\\\");
      else if(c<32 || (asciiOnly && c>=127))result.append(String.format("\\u%04X",(int)c));
      else result.append(c);
    }
    return result.append('"').toString();
  }
  private static String document(String value,boolean escaped){
    return "{\"externalId\":"+encoding(value,escaped)+",\"customerId\":\"c\",\"productCode\":\"p\"}";
  }
  private static String generated(){
    int[] alphabet={'A','z','7','"','\\','\n','\t',0x7f,0x80,0xe9,0x301,0x20ac,
                    0x2028,0x2603,0x1f680,0x1f600,0x10ffff};
    StringBuilder value=new StringBuilder();
    int length=1+random.nextInt(22);
    for(int i=0;i<length;i++)value.appendCodePoint(alphabet[random.nextInt(alphabet.length)]);
    return value.toString();
  }
  private static boolean rejected(String source){
    try{StrictJson.parse(source);return false;}
    catch(IllegalArgumentException expected){return true;}
  }
  public static void main(String[] unused){
    for(int i=0;i<1000;i++){
      String expected=generated();
      String literal=document(expected,false),escaped=document(expected,true);
      Map<String,String> a=StrictJson.parse(literal),b=StrictJson.parse(escaped);
      check(expected.equals(a.get("externalId")),"raw scalar preservation vector "+i);
      check(expected.equals(b.get("externalId")),"escaped scalar preservation vector "+i);
      check(a.equals(b),"two encodings must parse to same identity vector "+i);
    }
    for(int i=0;i<512;i++){
      int value=i%1024;
      String code=String.format("%04X",0xD800+value);
      String invalid="{\"externalId\":\"x\\u"+code+"\",\"customerId\":\"c\",\"productCode\":\"p\"}";
      check(rejected(invalid),"unpaired surrogate must fail " + i);
    }
    // Unknown-key and alias/duplicate-field invariants, with mixed key encodings.
    for(int i=0;i<100;i++){
      String randomValue=generated();
      String bad="{\"externalId\":"+encoding(randomValue,true)+
        ",\"exter\\u006ealId\":"+encoding(randomValue,false)+
        ",\"customerId\":\"c\",\"productCode\":\"p\"}";
      check(rejected(bad),"escaped duplicate keys denied " + i);
    }
    System.out.println("STRICT_JSON_PROPERTY_RESULT FAILURES "+failed+"/"+total+
      " PASSED "+(total-failed)+"/"+total+" seed="+SEED);
    if(failed!=0)System.exit(1);
  }
}
