package eu.freecollab.mobile;
import java.util.UUID;
/** OS PendingIntents can survive the Java process; integer epochs alone cannot. */
public final class FreeCollabCallIncarnation {
    private final String processNonce=UUID.randomUUID().toString();
    public String nonce(){return processNonce;}
    public boolean matches(int current,int expected,String expectedNonce){return current==expected&&processNonce.equals(expectedNonce);}
}
