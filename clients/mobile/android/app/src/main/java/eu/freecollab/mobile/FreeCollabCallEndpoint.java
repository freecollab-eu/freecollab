package eu.freecollab.mobile;
import java.net.URI;
import java.net.URL;
/** The shipped same-origin gateway mounts the backend below /api. */
public final class FreeCollabCallEndpoint {
    public static URL url(String origin,String path) throws Exception {
        URI base=new URI(origin);
        if(!"https".equals(base.getScheme())||base.getHost()==null||base.getRawUserInfo()!=null||base.getRawQuery()!=null||base.getRawFragment()!=null||!(base.getRawPath()==null||base.getRawPath().isEmpty()||base.getRawPath().equals("/"))||path.length()>512||!path.matches("[A-Za-z0-9][A-Za-z0-9/_-]*")||path.contains("//")||path.contains("..")) throw new IllegalArgumentException("Invalid calling endpoint");
        return new URL(origin.replaceAll("/$","")+"/api/mobile/native/calling/"+path);
    }
    private FreeCollabCallEndpoint(){}
}
