package org.dittobuffer.launcher;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.InputType;
import android.util.Base64;
import android.util.Log;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.CertificateFactory;
import java.util.concurrent.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.*;

/** App-owned onboarding and a loopback-to-TLS bridge. No root, shell or controller. */
public final class FamilyActivity extends Activity {
    private static final String GAME="com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity";
    static final String LOCAL="http://127.0.0.1:18080";
    private static final String KEY="dittobuffer.family.credential.v1";
    private static volatile Relay relay;
    private static boolean loaded;
    private JSONObject profile, credential;
    private EditText origin, username, password, invitation, trainer;
    private TextView status;
    private Spinner fixtureLocation;
    private Spinner graphics;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();

    // Material 3 light colour roles. The launcher is built from platform widgets
    // only, so the roles are applied through drawables instead of AndroidX.
    private static final int PRIMARY=0xFF6750A4, ON_PRIMARY=0xFFFFFFFF;
    private static final int SURFACE=0xFFFEF7FF, ON_SURFACE=0xFF1D1B20;
    private static final int SURFACE_LOW=0xFFF7F2FA, SURFACE_HIGH=0xFFECE6F0;
    private static final int ON_SURFACE_VARIANT=0xFF49454F;
    private static final int SECONDARY_CONTAINER=0xFFE8DEF8, ON_SECONDARY_CONTAINER=0xFF1D192B;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setFlags(0x2000,0x2000); // account entry is excluded from screenshots
        getWindow().setStatusBarColor(SURFACE);
        getWindow().setNavigationBarColor(SURFACE);
        if(android.os.Build.VERSION.SDK_INT>=26)
            getWindow().getDecorView().setSystemUiVisibility(
                android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        try { profile=new JSONObject(read(getAssets().open("dittobuffer/profile.json"),65536)); }
        catch(Exception e) { throw new IllegalStateException("Missing standalone profile",e); }

        ScrollView scroll=new ScrollView(this);scroll.setBackgroundColor(SURFACE);
        LinearLayout page=new LinearLayout(this);page.setOrientation(1);page.setPadding(dp(20),dp(24),dp(20),dp(32));
        scroll.addView(page);setContentView(scroll);

        // Top app bar: brand mark, headline, supporting line.
        LinearLayout bar=new LinearLayout(this);bar.setOrientation(0);bar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        TextView mark=new TextView(this);mark.setText("D");mark.setTextColor(ON_PRIMARY);
        mark.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP,22);
        mark.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);mark.setGravity(android.view.Gravity.CENTER);
        mark.setBackground(shape(PRIMARY,28));
        LinearLayout.LayoutParams markParams=new LinearLayout.LayoutParams(dp(48),dp(48));
        markParams.rightMargin=dp(16);bar.addView(mark,markParams);
        LinearLayout titles=new LinearLayout(this);titles.setOrientation(1);bar.addView(titles);
        titles.addView(text("Dittobuffer",24,ON_SURFACE,android.graphics.Typeface.DEFAULT_BOLD,0f));
        titles.addView(text("Private family server",14,ON_SURFACE_VARIANT,android.graphics.Typeface.DEFAULT,0.1f));
        page.addView(bar);

        LinearLayout serverCard=card("Server");
        origin=field(serverCard,"Server origin",false);
        origin.setText(getPreferences(0).getString("origin",profile.optString("origin")));
        serverCard.addView(hint("HTTPS origin of your family server, for example https://127.0.0.1:8445"));
        page.addView(serverCard);

        LinearLayout accountCard=card("Account");
        username=field(accountCard,"Account username",false);
        password=field(accountCard,"Password (12-128 characters)",true);
        trainer=field(accountCard,"Trainer name (registration only)",false);
        invitation=field(accountCard,"Invitation (registration only)",true);
        filledButton(accountCard,"Sign in",()->authenticate(false));
        tonalButton(accountCard,"Create invited account",()->authenticate(true));
        textButton(accountCard,"Continue saved account",()->continueSaved());
        textButton(accountCard,"Sign out and revoke session",()->logout());
        page.addView(accountCard);

        LinearLayout gameCard=card("Game");
        graphics=new Spinner(this);graphics.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,new String[]{"Graphics: automatic","Graphics: OpenGL ES 3"}));
        graphics.setSelection(getPreferences(0).getInt("graphics",0));gameCard.addView(graphics);
        if(profile.has("fixture_locations")) {
            fixtureLocation=new Spinner(this);java.util.ArrayList<String> names=new java.util.ArrayList<>();
            org.json.JSONArray sites=profile.optJSONArray("fixture_locations");for(int i=0;i<sites.length();i++)names.add(sites.optJSONObject(i).optString("name"));
            fixtureLocation.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names));
            fixtureLocation.setSelection(getPreferences(0).getInt("fixture_site",0));gameCard.addView(fixtureLocation);
            tonalButton(gameCard,"Apply isolated development location",()->{try {writeFixture();if(loaded)startActivity(new Intent().setClassName(getPackageName(),GAME));else message("Development location saved; no walking credit.");}catch(Exception e){message(safe(e));}});
        }
        if(profile.optBoolean("routes_foreground_creation",false)) {
            gameCard.addView(hint("Route recording uses location while this app is open and pauses when it leaves the foreground. Adventure Sync is not enabled by this mode."));
        }
        page.addView(gameCard);

        status=text("",13,ON_SURFACE_VARIANT,android.graphics.Typeface.DEFAULT,0.15f);
        status.setPadding(dp(4),dp(16),dp(4),dp(4));page.addView(status);
        message("Standalone build: embedded integration; HTTPS outside this phone.");
        page.postDelayed(this::maybeAutologin,300);
    }

    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent);setIntent(intent);maybeAutologin(); }

    private int dp(int value) { return Math.round(value*getResources().getDisplayMetrics().density); }

    private TextView text(String value,float size,int colour,android.graphics.Typeface typeface,float spacing) {
        TextView view=new TextView(this);view.setText(value);view.setTextColor(colour);view.setTypeface(typeface);
        view.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP,size);view.setLetterSpacing(spacing);
        return view;
    }

    private TextView hint(String value) {
        TextView view=text(value,12,ON_SURFACE_VARIANT,android.graphics.Typeface.DEFAULT,0.2f);
        view.setPadding(dp(4),dp(6),dp(4),dp(2));return view;
    }

    private android.graphics.drawable.GradientDrawable shape(int fill,int radius) {
        android.graphics.drawable.GradientDrawable drawable=new android.graphics.drawable.GradientDrawable();
        drawable.setColor(fill);drawable.setCornerRadius(dp(radius));return drawable;
    }

    private LinearLayout card(String title) {
        LinearLayout card=new LinearLayout(this);card.setOrientation(1);
        card.setBackground(shape(SURFACE_LOW,28));card.setPadding(dp(20),dp(20),dp(20),dp(16));
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin=dp(20);card.setLayoutParams(params);
        card.addView(text(title,16,ON_SURFACE,android.graphics.Typeface.DEFAULT_BOLD,0.1f));
        TextView spacer=new TextView(this);spacer.setHeight(dp(8));card.addView(spacer);
        return card;
    }

    private EditText field(LinearLayout box,String label,boolean secret) {
        TextView caption=text(label,12,ON_SURFACE_VARIANT,android.graphics.Typeface.DEFAULT,0.3f);
        caption.setPadding(dp(4),dp(10),dp(4),dp(4));box.addView(caption);
        EditText f=new EditText(this);f.setSingleLine();f.setHint(label);
        f.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP,16f);f.setTextColor(ON_SURFACE);f.setHintTextColor(ON_SURFACE_VARIANT);
        f.setBackground(shape(SURFACE_HIGH,12));f.setPadding(dp(16),dp(14),dp(16),dp(14));
        f.setInputType(secret?InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        f.setImportantForAutofill(android.view.View.IMPORTANT_FOR_AUTOFILL_NO);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT);
        params.bottomMargin=dp(4);box.addView(f,params);return f;
    }

    private Button baseButton(LinearLayout box,String label,int fill,int textColour) {
        Button b=new Button(this);b.setText(label);b.setTextColor(textColour);b.setAllCaps(false);
        b.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP,14f);b.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        b.setLetterSpacing(0.01f);b.setMinHeight(dp(48));b.setStateListAnimator(null);
        android.graphics.drawable.GradientDrawable background=shape(fill,24);
        android.graphics.drawable.RippleDrawable ripple=new android.graphics.drawable.RippleDrawable(
            android.content.res.ColorStateList.valueOf(0x33FFFFFF),background,null);
        b.setBackground(ripple);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin=dp(8);box.addView(b,params);return b;
    }

    private void filledButton(LinearLayout box,String label,Runnable action) {
        baseButton(box,label,PRIMARY,ON_PRIMARY).setOnClickListener(v->action.run());
    }

    private void tonalButton(LinearLayout box,String label,Runnable action) {
        baseButton(box,label,SECONDARY_CONTAINER,ON_SECONDARY_CONTAINER).setOnClickListener(v->action.run());
    }

    private void textButton(LinearLayout box,String label,Runnable action) {
        Button b=baseButton(box,label,0,PRIMARY);b.setBackgroundColor(0x00000000);b.setOnClickListener(v->action.run());
    }

    /** ADB-only repeat testing: am start ... --es dittobuffer.autologin <nonce>.
     *  The nonce is stored so Android restarting the task after a crash does not
     *  log in again in a loop. A fresh nonce triggers exactly one attempt. */
    private void maybeAutologin() {
        if(loaded)return;
        String nonce=getIntent().getStringExtra("dittobuffer.autologin");
        if(nonce==null&&getIntent().getBooleanExtra("dittobuffer.autologin",false))nonce="1";
        if(nonce==null||nonce.isEmpty()){
            // Opt-in automatic continuation uses only this installation's saved credential.
            // A recovery launch can show account controls with dittobuffer.show_login=true.
            if(profile.optBoolean("auto_continue_saved",false)
                    &&getPreferences(0).contains("credential")
                    &&!getIntent().getBooleanExtra("dittobuffer.show_login",false))continueSaved();
            return;
        }
        getIntent().removeExtra("dittobuffer.autologin");
        if(nonce.equals(getPreferences(0).getString("autologin_nonce","")))return;
        getPreferences(0).edit().putString("autologin_nonce",nonce).commit();
        // ADB-only sign-in for fresh installs that have no saved credential yet:
        // am start ... --es dittobuffer.autologin <nonce> --es dittobuffer.username u
        //   --es dittobuffer.password p [--es dittobuffer.register 1] [--es dittobuffer.trainer t]
        String user=getIntent().getStringExtra("dittobuffer.username");
        String pass=getIntent().getStringExtra("dittobuffer.password");
        if(user!=null&&!user.isEmpty()&&pass!=null&&!pass.isEmpty()){
            username.setText(user);password.setText(pass);
            String name=getIntent().getStringExtra("dittobuffer.trainer");
            String invite=getIntent().getStringExtra("dittobuffer.invitation");
            if(name!=null)trainer.setText(name);
            if(invite!=null)invitation.setText(invite);
            authenticate(getIntent().getBooleanExtra("dittobuffer.register",false));
            return;
        }
        continueSaved();
    }
    private void message(String text) { runOnUiThread(()->status.setText(text)); }
    private String checkedOrigin() throws Exception {
        String value=origin.getText().toString().trim().replaceAll("/+$","");URI uri=new URI(value);
        String host=uri.getHost();
        if(!"https".equals(uri.getScheme())||host==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null||!(uri.getPath().isEmpty()||uri.getPath().equals("/")))throw new IOException("Use an HTTPS origin without credentials or a path");
        if(host.equals("nianticlabs.com")||host.endsWith(".nianticlabs.com")||host.equals("pokemongolive.com")||host.endsWith(".pokemongolive.com"))throw new IOException("A private Dittobuffer server is required");
        return value;
    }
    private void authenticate(boolean register) {
        try {
            final String server=checkedOrigin();
            final JSONObject data=new JSONObject().put("username",username.getText().toString()).put("password",password.getText().toString()).put("device_label",android.os.Build.MODEL+" standalone");
            if(register)data.put("trainer_name",trainer.getText().toString()).put("invitation",invitation.getText().toString());
            password.setText("");invitation.setText("");message("Contacting private server…");
            worker.execute(()->{try {
                if(register)request(server,"/accounts/register",data);
                JSONObject result=request(server,"/accounts/login",data);
                credential=result;saveCredential(result,server);startGame(server,result);
            }catch(Exception e){message("Sign-in failed: "+safe(e));}});
        }catch(Exception e){message(safe(e));}
    }
    private void continueSaved() {
        message("Opening saved account…");
        worker.execute(()->{try {
            JSONObject saved=loadCredential();String server=checkedOrigin();
            if(!server.equals(saved.getString("origin")))throw new IOException("Saved account belongs to a different origin; sign in again");
            credential=saved.getJSONObject("credential");startGame(server,credential);
        }catch(Exception e){message("Saved account unavailable: "+safe(e));}});
    }
    private void logout() {
        worker.execute(()->{try {
            JSONObject saved=loadCredential();request(saved.getString("origin"),"/accounts/logout",new JSONObject().put("token",saved.getJSONObject("credential").getString("token")));
            getPreferences(0).edit().remove("credential").apply();new File(getFilesDir(),"dittobuffer-family.json").delete();
            message("Session revoked. Close the game before signing into another account.");
        }catch(Exception e){message("Logout failed: "+safe(e));}});
    }
    private void startGame(String server,JSONObject auth) throws Exception {
        JSONObject health=request(server,"/health",null);
        if(!"family".equals(health.optString("authentication"))||!"0.429.2".equals(health.optString("target")))throw new IOException("Expected a 0.429.2 family server");
        String expectedCatalog=profile.optString("runtime_catalog_sha256");
        if(!expectedCatalog.isEmpty()&&(!health.optBoolean("client_resources")||!expectedCatalog.equals(health.optString("client_catalog_sha256"))))
            throw new IOException("Server client resources do not match this APK. Enable its verified runtime registry before starting the game.");
        if(!auth.optString("token").matches("lf1_[A-Za-z0-9_-]{43}"))throw new IOException("Invalid account credential");
        if(loaded)throw new IOException("Restart the application before changing accounts or server");
        relay=new Relay(server,this);relay.start();
        // The maintained native transport reads dittobuffer-server.url. Retain
        // librenlp-server.url for older compatible local native artifacts;
        // the packaged integration adapter reads dittobuffer-server.url. Both must
        // name the loopback relay, otherwise the transport falls back to its
        // development default and authentication silently never completes.
        write("librenlp-server.url",LOCAL);write("dittobuffer-server.url",LOCAL);write("dittobuffer-family.json",auth.toString());
        JSONObject runtime=new JSONObject(profile.toString()).put("local_origin",LOCAL).put("family",true).put("world",health.optBoolean("world",true));
        runtime.put("pokemon_assets",health.optBoolean("pokemon_assets")).put("avatar_assets",health.optBoolean("avatar_assets"));
        runtime.put("client_resources",health.optBoolean("client_resources"));
        if(runtime.has("fixture_origin")&&!health.optString("profile").equals(runtime.optString("fixture_profile")))throw new IOException("Development location fixture does not match this isolated server");
        write("dittobuffer-bootstrap.json",runtime.toString());
        int graphicsMode=graphics.getSelectedItemPosition();getPreferences(0).edit().putInt("graphics",graphicsMode).commit();
        if(fixtureLocation!=null)writeFixture();
        File runtimeDir=new File(getCodeCacheDir(),"dittobuffer");if(!runtimeDir.exists()&&!runtimeDir.mkdir())throw new IOException("Cannot prepare embedded runtime");
        for(String name:new String[]{"libdittobuffer_runtime.so","libdittobuffer_runtime.config.so","agent.js"}) {
            File target=new File(runtimeDir,name);
            if(target.exists()&&!target.delete())throw new IOException("Cannot replace embedded runtime");
            try(InputStream in=getAssets().open("dittobuffer/"+name);FileOutputStream out=new FileOutputStream(target)) {
                target.setReadOnly();byte[] buf=new byte[32768];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);out.getFD().sync();
            }
        }
        File marker=new File(getFilesDir(),"dittobuffer-bootstrap-status");marker.delete();
        // Map the supplied IL2CPP image before observing it. Its managed domain
        // is initialized later by Unity, after the autonomous hooks are ready.
        System.loadLibrary("il2cpp");
        System.load(new File(runtimeDir,"libdittobuffer_runtime.so").getAbsolutePath());
        for(int i=0;i<200&&!marker.exists();i++)Thread.sleep(100);
        if(!marker.exists())throw new IOException("Embedded integration failed to configure; game remains closed");loaded=true;
        Log.i("DittobufferStandalone","Embedded integration loaded; no external controller");
        runOnUiThread(()->{getWindow().clearFlags(0x2000);Intent intent=new Intent().setClassName(getPackageName(),GAME);if(graphicsMode==1)intent.putExtra("unity","-force-gles30");startActivity(intent);});
    }
    private void write(String name,String data) throws IOException { try(FileOutputStream s=openFileOutput(name,MODE_PRIVATE)){s.write(data.getBytes(StandardCharsets.UTF_8));s.getFD().sync();} }
    private void writeFixture() throws Exception {
        int selected=fixtureLocation.getSelectedItemPosition();JSONObject site=profile.getJSONArray("fixture_locations").getJSONObject(selected);
        getPreferences(0).edit().putInt("fixture_site",selected).commit();write("dittobuffer-fixture-location.json",site.toString());
    }
    private javax.crypto.SecretKey key() throws Exception {
        KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);
        if(!ks.containsAlias(KEY)) {KeyGenerator g=KeyGenerator.getInstance("AES","AndroidKeyStore");g.init(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());g.generateKey();}
        return (javax.crypto.SecretKey)ks.getKey(KEY,null);
    }
    private void saveCredential(JSONObject auth,String server) throws Exception {
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key());
        JSONObject secret=new JSONObject().put("origin",server).put("credential",auth);
        String value=Base64.encodeToString(c.getIV(),Base64.NO_WRAP)+":"+Base64.encodeToString(c.doFinal(secret.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
        getPreferences(0).edit().putString("credential",value).putString("origin",server).commit();
    }
    private JSONObject loadCredential() throws Exception {
        String value=getPreferences(0).getString("credential","");String[] parts=value.split(":");
        if(parts.length!=2)throw new IOException("No saved account");
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(parts[0],Base64.NO_WRAP)));
        return new JSONObject(new String(c.doFinal(Base64.decode(parts[1],Base64.NO_WRAP)),StandardCharsets.UTF_8));
    }
    private static String safe(Exception e) {return e instanceof IOException||e instanceof IllegalArgumentException?e.getMessage():e.getClass().getSimpleName();}
    JSONObject request(String server,String path,JSONObject data) throws Exception {
        HttpsURLConnection c=connection(server,path);
        if(data!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");try(OutputStream s=c.getOutputStream()){s.write(data.toString().getBytes(StandardCharsets.UTF_8));}}
        int status=c.getResponseCode();if(status!=200&&status!=201){c.disconnect();throw new IOException("Server rejected request ("+status+")");}
        try(InputStream s=c.getInputStream()){return new JSONObject(read(s,65536));}finally{c.disconnect();}
    }
    HttpsURLConnection connection(String server,String path) throws Exception {
        if(!path.startsWith("/")||path.startsWith("//")||path.contains("\r")||path.contains("\n"))throw new IOException("Invalid resource path");
        HttpsURLConnection c=(HttpsURLConnection)new URL(server+path).openConnection();c.setInstanceFollowRedirects(false);c.setConnectTimeout(10000);c.setReadTimeout(40000);
        if(server.equals(profile.optString("origin"))&&profile.has("ca_asset")) {
            KeyStore trust=KeyStore.getInstance(KeyStore.getDefaultType());trust.load(null);
            try(InputStream s=getAssets().open(profile.getString("ca_asset"))){trust.setCertificateEntry("private-server",CertificateFactory.getInstance("X.509").generateCertificate(s));}
            TrustManagerFactory tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());tm.init(trust);
            SSLContext ctx=SSLContext.getInstance("TLS");ctx.init(null,tm.getTrustManagers(),null);c.setSSLSocketFactory(ctx.getSocketFactory());
        }
        // Default hostname verifier remains enabled, including IP SAN validation.
        return c;
    }
    static String read(InputStream in,int limit) throws IOException {ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1){if(b.size()+n>limit)throw new IOException("Response too large");b.write(buf,0,n);}return b.toString("UTF-8");}

    /** Only app-loopback HTTP. Every off-device byte uses scoped, verified HTTPS. */
    static final class Relay {
        final String remote;final FamilyActivity owner;final ServerSocket socket;
        final ExecutorService pool=Executors.newFixedThreadPool(16);
        Relay(String remote,FamilyActivity owner) throws IOException {this.remote=remote;this.owner=owner;socket=new ServerSocket();socket.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"),18080),32);}
        void start() {Thread t=new Thread(()->{while(!socket.isClosed()){try {Socket s=socket.accept();s.setSoTimeout(40000);pool.execute(()->serve(s));}catch(IOException e){Log.w("DittobufferStandalone","Loopback accept failed");break;}}},"dittobuffer-tls-relay");t.setDaemon(true);t.start();}
        static String line(InputStream in) throws IOException {ByteArrayOutputStream b=new ByteArrayOutputStream();int v;while((v=in.read())!=-1){if(v==10)break;if(v!=13)b.write(v);if(b.size()>16384)throw new IOException("Header too long");}return b.toString("US-ASCII");}
        void serve(Socket s) {
            HttpsURLConnection upstream=null;
            try(Socket local=s) {
                InputStream in=local.getInputStream();OutputStream out=local.getOutputStream();String[] first=line(in).split(" ");
                if(first.length!=3||!(first[0].equals("GET")||first[0].equals("POST")))throw new IOException("Invalid method");
                String path=first[1];if(path.startsWith("/accounts/"))throw new IOException("Account endpoints require the app UI");
                upstream=owner.connection(remote,path);upstream.setRequestMethod(first[0]);int size=0;boolean expect=false;
                for(int count=0;count<64;count++) {
                    String header=line(in);if(header.isEmpty())break;int colon=header.indexOf(':');if(colon<1)throw new IOException("Malformed header");
                    String name=header.substring(0,colon),value=header.substring(colon+1).trim();
                    if(name.equalsIgnoreCase("Content-Length"))size=Integer.parseInt(value);
                    else if(name.equalsIgnoreCase("Transfer-Encoding"))throw new IOException("Chunked request unsupported");
                    else if(name.equalsIgnoreCase("Expect"))expect=value.equalsIgnoreCase("100-continue");
                    else if(!name.equalsIgnoreCase("Host")&&!name.equalsIgnoreCase("Connection"))upstream.setRequestProperty(name,value);
                }
                if(size<0||size>16*1024*1024)throw new IOException("Request too large");
                if(expect){out.write("HTTP/1.1 100 Continue\r\n\r\n".getBytes(StandardCharsets.US_ASCII));out.flush();}
                byte[] buf=new byte[32768];int n;
                if(first[0].equals("POST")) {upstream.setDoOutput(true);upstream.setFixedLengthStreamingMode(size);try(OutputStream dst=upstream.getOutputStream()){int left=size;while(left>0){n=in.read(buf,0,Math.min(left,buf.length));if(n<0)throw new EOFException();dst.write(buf,0,n);left-=n;}}}
                int status=upstream.getResponseCode();out.write(("HTTP/1.1 "+status+" Response\r\nConnection: close\r\n").getBytes(StandardCharsets.US_ASCII));
                for(java.util.Map.Entry<String,java.util.List<String>> h:upstream.getHeaderFields().entrySet()) {
                    if(h.getKey()==null||h.getKey().equalsIgnoreCase("Transfer-Encoding")||h.getKey().equalsIgnoreCase("Connection"))continue;
                    for(String value:h.getValue())out.write((h.getKey()+": "+value+"\r\n").getBytes(StandardCharsets.US_ASCII));
                }
                out.write("\r\n".getBytes(StandardCharsets.US_ASCII));out.flush();
                InputStream body=status>=400?upstream.getErrorStream():upstream.getInputStream();
                if(body!=null)try(InputStream src=body){while((n=src.read(buf))!=-1){out.write(buf,0,n);}out.flush();}
            }catch(Exception e){Log.w("DittobufferStandalone","TLS relay request failed: "+e.getClass().getSimpleName());}
            finally{if(upstream!=null)upstream.disconnect();}
        }
    }
}
