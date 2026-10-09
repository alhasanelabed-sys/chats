package com.majlis.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A real recorder and an explicitly configured remote analysis client. */
public class MainActivity extends Activity {
    private static final String ANALYSIS_UPDATE="com.majlis.app.ANALYSIS_UPDATE";
    private static volatile boolean analysisRunning;
    private static final int INK = Color.rgb(19,47,54), TEAL = Color.rgb(8,127,114);
    private static final int MUTED = Color.rgb(101,117,120), PAPER = Color.rgb(244,243,238);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private MeetingStore store;
    private SharedPreferences prefs;
    private LinearLayout page, content;
    private TextView timer, status;
    private ProgressBar level;
    private EditText titleField;
    private CheckBox consent;
    private Button recordButton, analyzeButton;
    private String screen = "meeting";
    private boolean busy;
    private Runnable afterPermission;
    private JSONObject result;
    private String resultTab = "minutes";
    private MediaRecorder sampleRecorder;
    private MediaPlayer player;
    private File sampleFile;
    private AlertDialog sampleDialog;
    private long sampleStarted;
    private Runnable sampleTick;

    private final BroadcastReceiver recorderUpdates = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent intent) {
            if(ANALYSIS_UPDATE.equals(intent.getAction())) {
                busy=analysisRunning;
                if("meeting".equals(screen))showMeeting();
                if(intent.hasExtra("error"))error(intent.getStringExtra("error"));
                else if(intent.getBooleanExtra("saved",false))toast("تم حفظ المحضر في تبويب المحاضر.");
                return;
            }
            String state = intent.getStringExtra("state");
            if ("recording".equals(state)) {
                updateRecording(intent.getLongExtra("elapsed_ms",0), intent.getIntExtra("amplitude",0));
            } else if ("stopped".equals(state)) {
                String path = intent.getStringExtra("path");
                if (path != null) prefs.edit().putString("last_audio",path).apply();
                if ("duration_limit".equals(intent.getStringExtra("reason"))) toast("اكتمل الحد الأقصى: 20 دقيقة.");
                if ("meeting".equals(screen)) showMeeting();
            } else if ("error".equals(state)) {
                prefs.edit().remove("last_audio").apply();
                if ("meeting".equals(screen)) showMeeting();
                error(intent.getStringExtra("error"));
            }
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        store = new MeetingStore(this);
        prefs = getSharedPreferences("majlis",MODE_PRIVATE);
        if(prefs.getBoolean("recording_incomplete",false)&&!RecordingService.isRecording()) {
            File interrupted=new File(prefs.getString("last_audio",""));
            try{if(interrupted.getCanonicalFile().getParentFile().equals(store.recordingsDir().getCanonicalFile()))interrupted.delete();}catch(Exception ignored){}
            prefs.edit().remove("last_audio").putBoolean("recording_incomplete",false).commit();
            toast("انقطع التسجيل السابق قبل اكتماله. ابدأ تسجيلًا جديدًا.");
        }
        IntentFilter filter = new IntentFilter(RecordingService.ACTION_UPDATE);
        filter.addAction(ANALYSIS_UPDATE);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(recorderUpdates,filter,Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(recorderUpdates,filter);
        showMeeting();
    }

    @Override public void onResume() {
        super.onResume();
        if ("meeting".equals(screen)) showMeeting();
    }

    @Override public void onPause() {
        if (titleField != null && "meeting".equals(screen)) prefs.edit().putString("last_title",titleField.getText().toString()).apply();
        finishSample(false,null);
        stopPlayer();
        super.onPause();
    }

    @Override public void onDestroy() {
        unregisterReceiver(recorderUpdates);
        finishSample(false,null);
        worker.shutdown();
        super.onDestroy();
    }

    private void shell(String subtitle) {
        page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        page.setLayoutDirection(View.LAYOUT_DIRECTION_RTL); page.setBackgroundColor(PAPER); page.setFitsSystemWindows(true);
        LinearLayout header = new LinearLayout(this); header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(dp(24),dp(24),dp(24),dp(20)); header.setBackgroundColor(INK);
        TextView brand = label("مجلس",32,Color.WHITE,true); header.addView(brand);
        header.addView(label(subtitle,14,Color.rgb(194,220,216),false));
        page.addView(header);
        LinearLayout nav = new LinearLayout(this); nav.setPadding(dp(8),dp(6),dp(8),0);
        nav.addView(navButton("الاجتماع",()->showMeeting()));
        nav.addView(navButton("المحاضر",()->showHistory()));
        nav.addView(navButton("الأصوات",()->showVoices()));
        nav.addView(navButton("الإعدادات",()->showSettings()));
        page.addView(nav);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(18),dp(16),dp(18),dp(30));
        scroll.addView(content);
        page.addView(scroll,new LinearLayout.LayoutParams(-1,0,1)); setContentView(page);
    }

    private Button navButton(String title,Runnable action) {
        Button b = button(title,false,action); b.setTextSize(12); b.setPadding(0,0,0,0);
        b.setLayoutParams(new LinearLayout.LayoutParams(0,dp(44),1)); return b;
    }

    private void showMeeting() {
        busy=analysisRunning;
        screen="meeting"; shell("من حديث الاجتماع إلى محضر قابل للمراجعة");
        LinearLayout card = card();
        card.addView(label("اجتماع جديد",23,INK,true));
        card.addView(label("تسجيل عربي • فصل المتحدثين • محضر وملخص",13,MUTED,false));
        titleField = edit("عنوان الاجتماع",prefs.getString("last_title","اجتماع الفريق"),false);
        titleField.setEnabled(!RecordingService.isRecording() && !busy); card.addView(titleField);
        timer=label("00:00",48,INK,true); timer.setGravity(Gravity.CENTER);
        timer.setTextDirection(View.TEXT_DIRECTION_LTR); card.addView(timer);
        status=label("جاهز لتسجيل اجتماعك",14,MUTED,false); status.setGravity(Gravity.CENTER); card.addView(status);
        level = new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal); level.setMax(32767);
        level.setProgressTintList(android.content.res.ColorStateList.valueOf(TEAL));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1,dp(8)); lp.setMargins(0,dp(16),0,dp(18)); card.addView(level,lp);
        consent=new CheckBox(this); consent.setText("أعلمت المشاركين بالتسجيل وحصلت على موافقتهم"); consent.setTextSize(14); consent.setTextColor(INK);
        consent.setChecked(RecordingService.isRecording()); consent.setEnabled(!RecordingService.isRecording() && !busy); card.addView(consent);
        recordButton=button(RecordingService.isRecording()?"إنهاء التسجيل":"بدء التسجيل",true,()->recordTap());
        recordButton.setEnabled(!busy); card.addView(recordButton);
        File last=lastAudio();
        if (!RecordingService.isRecording() && last != null) {
            status.setText("التسجيل محفوظ على الجهاز • جاهز للتحليل");
            card.addView(button("استماع للتسجيل",false,()->play(last)));
        }
        analyzeButton=button(busy?"جارٍ تحليل الاجتماع…":"إعداد المحضر والملخص",true,()->analyze());
        analyzeButton.setEnabled(!RecordingService.isRecording() && last != null && !busy); card.addView(analyzeButton);
        if (busy) { ProgressBar progress=new ProgressBar(this); card.addView(progress); }
        card.addView(label("الحد الأقصى للتسجيل 20 دقيقة. يبقى التسجيل محليًا حتى توافق على إرساله للتحليل.",12,MUTED,false));
        content.addView(card);
        LinearLayout info=card(); info.addView(label("لتمييز الأسماء",19,INK,true));
        JSONArray voices=store.profiles();
        info.addView(label(voices.length()==0?"أضف عينة قصيرة باسم كل مشارك من تبويب الأصوات، أو سمِّ المتحدثين بعد التحليل.":"لديك "+voices.length()+" عينات صوت مسماة ستُستخدم عند التحليل.",14,MUTED,false));
        info.addView(label("من ينضم لاحقًا يظهر بعد أن يتكلم بصوت واضح. تمييز المتحدثين يجري بعد إنهاء التسجيل، وقد يحتاج تصحيحًا عند التداخل.",14,MUTED,false));
        info.addView(label("للأصوات البعيدة ضع الهاتف وسط الطاولة، أو استخدم ميكروفون اجتماعات. التطبيق لا يضمن التقاط كلام لا يصل بوضوح إلى الميكروفون.",14,MUTED,false));
        content.addView(info);
        Button demo=button("تجربة اجتماع بمحتوى تجريبي",false,()->showDemo()); demo.setEnabled(!busy); content.addView(demo);
        updateRecording(RecordingService.elapsedMillis(),0);
    }

    private void updateRecording(long elapsed,int amplitude) {
        if (!"meeting".equals(screen) || timer==null) return;
        if (RecordingService.isRecording()) {
            timer.setText(time(elapsed/1000.0)); status.setText("● التسجيل جارٍ — يمكن قفل الشاشة"); status.setTextColor(TEAL);
            recordButton.setText("إنهاء التسجيل"); recordButton.setEnabled(!busy); analyzeButton.setEnabled(false);
            consent.setEnabled(false); titleField.setEnabled(false); level.setProgress(amplitude);
        }
    }

    private void recordTap() {
        if (RecordingService.isRecording()) {
            startService(new Intent(this,RecordingService.class).setAction(RecordingService.ACTION_STOP)); return;
        }
        if (!consent.isChecked()) { toast("يلزم إعلام المشاركين وموافقتهم قبل التسجيل."); return; }
        if (titleField.getText().toString().trim().isEmpty()) { titleField.setError("أدخل عنوان الاجتماع"); return; }
        if (lastAudio()!=null) {
            new AlertDialog.Builder(this).setTitle("بدء تسجيل جديد")
                .setMessage("يوجد تسجيل لم يُحلل بعد. بدء تسجيل جديد سيحذف هذا التسجيل السابق.")
                .setNegativeButton("رجوع",null).setPositiveButton("حذف السابق والبدء",(d,w)->{ File f=lastAudio(); if(f!=null&&!f.delete()){error("تعذر حذف التسجيل السابق.");return;} prefs.edit().remove("last_audio").apply(); withMicrophone(()->startRecording()); }).show();
        } else withMicrophone(()->startRecording());
    }

    private void startRecording() {
        stopPlayer();
        String title=titleField.getText().toString().trim();
        File file=new File(store.recordingsDir(),"meeting_"+System.currentTimeMillis()+".m4a");
        prefs.edit().putString("last_title",title).putString("last_audio",file.getAbsolutePath()).apply();
        Intent start=new Intent(this,RecordingService.class).setAction(RecordingService.ACTION_START).putExtra("path",file.getAbsolutePath());
        try { startForegroundService(start); status.setText("جارٍ بدء التسجيل…"); recordButton.setEnabled(false); }
        catch(Exception ex) { prefs.edit().remove("last_audio").apply();error("تعذر بدء التسجيل: "+ex.getMessage());showMeeting(); }
    }

    private void withMicrophone(Runnable action) {
        boolean mic=checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED;
        boolean notifications=Build.VERSION.SDK_INT<33||checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED;
        if(mic&&notifications) { action.run(); return; }
        afterPermission=action;
        requestPermissions(Build.VERSION.SDK_INT>=33?new String[]{Manifest.permission.RECORD_AUDIO,Manifest.permission.POST_NOTIFICATIONS}:new String[]{Manifest.permission.RECORD_AUDIO},11);
    }

    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grants) {
        super.onRequestPermissionsResult(code,permissions,grants);
        if(code==11) {
            Runnable next=afterPermission; afterPermission=null;
            if(grants.length>0&&grants[0]==PackageManager.PERMISSION_GRANTED&&next!=null) {
                if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)toast("إذن الإشعارات غير مفعّل؛ أوقف التسجيل من داخل التطبيق.");
                next.run();
            }
            else toast("أذن الميكروفون مطلوب للتسجيل. يمكنك تجربة الاجتماع التجريبي.");
        }
    }

    private File lastAudio() {
        if(prefs.getBoolean("recording_incomplete",false))return null;
        String path=prefs.getString("last_audio",""); if(path.isEmpty())return null;
        File f=new File(path);
        try { if(!f.getCanonicalFile().getParentFile().equals(store.recordingsDir().getCanonicalFile())) return null; }
        catch(Exception ex){return null;}
        return f.exists()&&f.length()>0?f:null;
    }

    private void analyze() {
        if(busy||RecordingService.isRecording())return;
        File audio=lastAudio();if(audio==null){toast("سجل الاجتماع أولًا.");return;}
        final String url=prefs.getString("server_url","").trim(),token=prefs.getString("access_token","").trim();
        if(url.isEmpty()||token.isEmpty()) {
            new AlertDialog.Builder(this).setTitle("إعداد خدمة التحليل")
                .setMessage("أدخل رابط خادم المعالجة ورمز الوصول في الإعدادات. لا يحتاج التطبيق إلى مفتاح مزود الذكاء الاصطناعي.")
                .setPositiveButton("فتح الإعدادات",(d,w)->showSettings()).setNegativeButton("رجوع",null).show();return;
        }
        final JSONArray profiles=store.profiles();
        final String title=titleField.getText().toString().trim();
        new AlertDialog.Builder(this).setTitle("إرسال الاجتماع للتحليل؟")
            .setMessage("سيُرسل تسجيل الاجتماع و"+profiles.length()+" عينات صوت مسماة إلى:\n"+url+"\nوسيستخدم الخادم خدمة OpenAI لتفريغ الكلام وتمييز المتحدثين وإعداد الملخص. قد تُحتسب تكلفة على حساب صاحب الخادم. يبقى المحضر قابلًا للمراجعة.")
            .setNegativeButton("إلغاء",null).setPositiveButton("موافق، أرسل للتحليل",(d,w)->{
                if(analysisRunning)return;
                analysisRunning=true;busy=true;prefs.edit().putString("last_title",title).apply();showMeeting();
                worker.execute(()->{
                    try {
                        JSONObject analyzed=MeetingApi.analyze(url,token,audio,title,profiles);
                        analyzed.put("_audio_path",audio.getAbsolutePath()); analyzed.put("_demo",false);
                        store.saveMeeting(analyzed);
                        handler.post(()->{
                            analysisRunning=false;busy=false;
                            if(audio.getAbsolutePath().equals(prefs.getString("last_audio","")))prefs.edit().remove("last_audio").apply();
                            getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("saved",true));
                            if(!isFinishing()&&!isDestroyed()){result=analyzed;resultTab="minutes";showResult();}
                        });
                    } catch(Exception ex) { handler.post(()->{
                        analysisRunning=false;busy=false;
                        getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("error",ex.getMessage()==null?"تعذر تحليل الاجتماع.":ex.getMessage()));
                    }); }
                });
            }).show();
    }

    private void showVoices() {
        screen="voices";shell("عينات صوت مسماة لتحسين التعرف على المشاركين");
        LinearLayout intro=card();intro.addView(label("أسماء يعرفها اجتماعك",23,INK,true));
        intro.addView(label("سجّل 5 ثوانٍ بصوت كل مشارك، بموافقته، في مكان هادئ. تُحفظ العينة على الجهاز وتُرسل فقط عند موافقتك على تحليل اجتماع. هذه عينات مرجعية، وليست إثباتًا لهوية الشخص.",14,MUTED,false));
        intro.addView(label("يدعم النموذج حتى 4 عينات مسماة. يُرقَّم المتحدثون الإضافيون ويمكن تسميتهم بعد التحليل.",13,MUTED,false));
        Button add=button("إضافة عينة صوت",true,()->addVoice()); add.setEnabled(!RecordingService.isRecording()&&!busy&&store.profiles().length()<4);intro.addView(add);content.addView(intro);
        JSONArray profiles=store.profiles();
        for(int i=0;i<profiles.length();i++) {
            JSONObject profile=profiles.optJSONObject(i);if(profile==null)continue;
            LinearLayout row=card();row.addView(label(profile.optString("name"),20,INK,true));row.addView(label("عينة مرجعية • 5 ثوانٍ • محفوظة محليًا",12,MUTED,false));
            row.addView(button("استماع للعينة",false,()->play(new File(profile.optString("path")))));
            Button remove=button("حذف العينة",false,()->deleteVoice(profile));remove.setEnabled(!busy);row.addView(remove);content.addView(row);
        }
    }

    private void addVoice() {
        if(RecordingService.isRecording()||busy||store.profiles().length()>=4)return;
        EditText name=edit("اسم المشارك","",false);
        new AlertDialog.Builder(this).setTitle("عينة بموافقة صاحب الصوت")
            .setMessage("أدخل اسمه، ثم دعه يتحدث وحده لمدة 5 ثوانٍ. مثال: أنا أحمد وأشارك في اجتماع الفريق اليوم.")
            .setView(name).setNegativeButton("إلغاء",null).setPositiveButton("بدء عينة 5 ثوانٍ",(d,w)->{
                String n=name.getText().toString().trim();
                if(n.isEmpty()||n.length()>64){toast("أدخل اسمًا من 1 إلى 64 حرفًا.");return;}
                JSONArray all=store.profiles();for(int i=0;i<all.length();i++){if(n.equals(all.optJSONObject(i).optString("name"))){toast("هذا الاسم مسجل بالفعل.");return;}}
                withMicrophone(()->startSample(n));
            }).show();
    }

    @SuppressWarnings("deprecation") private void startSample(String name) {
        if(RecordingService.isRecording()||sampleRecorder!=null)return;
        stopPlayer();sampleFile=new File(store.recordingsDir(),"ref_"+System.currentTimeMillis()+".m4a");
        try {
            sampleRecorder=new MediaRecorder();sampleRecorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            sampleRecorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);sampleRecorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            sampleRecorder.setAudioChannels(1);sampleRecorder.setAudioSamplingRate(44100);sampleRecorder.setAudioEncodingBitRate(128000);
            sampleRecorder.setOutputFile(sampleFile.getAbsolutePath());sampleRecorder.prepare();sampleRecorder.start();sampleStarted=android.os.SystemClock.elapsedRealtime();
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            sampleDialog=new AlertDialog.Builder(this).setTitle("تحدث الآن يا "+name).setMessage("جارٍ تسجيل العينة: 5 ثوانٍ")
                .setNegativeButton("إلغاء",(d,w)->finishSample(false,null)).create();sampleDialog.setCancelable(false);sampleDialog.show();
            sampleTick=new Runnable(){@Override public void run(){if(sampleRecorder==null)return;long elapsed=android.os.SystemClock.elapsedRealtime()-sampleStarted;if(elapsed>=5000){finishSample(true,name);}else{if(sampleDialog!=null)sampleDialog.setMessage("تحدث وحدك بوضوح… متبقي "+(5-elapsed/1000)+" ثوانٍ");handler.postDelayed(this,250);}}};handler.post(sampleTick);
        } catch(Exception ex) { finishSample(false,null);error("تعذر تسجيل العينة: "+ex.getMessage()); }
    }

    private void finishSample(boolean keep,String name) {
        if(sampleTick!=null){handler.removeCallbacks(sampleTick);sampleTick=null;}
        if(sampleRecorder==null)return;
        MediaRecorder recorder=sampleRecorder;sampleRecorder=null;boolean success=false;
        try{recorder.stop();success=true;}catch(Exception ignored){}finally{recorder.release();}
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if(sampleDialog!=null){sampleDialog.dismiss();sampleDialog=null;}
        File f=sampleFile;sampleFile=null;
        if(keep&&success&&f!=null&&f.length()>0){
            try{JSONArray all=store.profiles();JSONObject p=new JSONObject();p.put("id","p_"+System.currentTimeMillis());p.put("name",name);p.put("path",f.getAbsolutePath());all.put(p);store.saveProfiles(all);if("voices".equals(screen))showVoices();toast("تم حفظ عينة "+name);}
            catch(Exception ex){if(f!=null)f.delete();error("تعذر حفظ العينة.");}
        }else if(f!=null)f.delete();
    }

    private void deleteVoice(JSONObject profile) {
        new AlertDialog.Builder(this).setTitle("حذف عينة "+profile.optString("name")+"؟")
            .setMessage("سيُحذف تسجيل العينة من هذا الجهاز.").setNegativeButton("إلغاء",null).setPositiveButton("حذف",(d,w)->{
                try{JSONArray old=store.profiles(),next=new JSONArray();for(int i=0;i<old.length();i++){JSONObject p=old.optJSONObject(i);if(!p.optString("id").equals(profile.optString("id")))next.put(p);}
                    store.saveProfiles(next);File f=new File(profile.optString("path"));if(f.getCanonicalFile().getParentFile().equals(store.recordingsDir().getCanonicalFile()))f.delete();stopPlayer();showVoices();
                }catch(Exception ex){error("تعذر حذف العينة.");}
            }).show();
    }

    private void showSettings() {
        screen="settings";shell("إعداد خدمة المعالجة والتحكم في بياناتك");
        LinearLayout card=card();card.addView(label("خدمة التحليل",23,INK,true));
        card.addView(label("يشغّل صاحب التطبيق الخادم المرفق، ويضبط مفتاح OpenAI عليه. أدخل هنا رابط الخادم ورمز الوصول الخاص بالتطبيق.",14,MUTED,false));
        EditText url=edit("https://your-server.example",prefs.getString("server_url",""),false);url.setTextDirection(View.TEXT_DIRECTION_LTR);url.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);card.addView(url);
        EditText token=edit("رمز وصول الخادم",prefs.getString("access_token",""),true);token.setTextDirection(View.TEXT_DIRECTION_LTR);card.addView(token);
        card.addView(button("حفظ الإعدادات",true,()->{
            try{String u=MeetingApi.validateBaseUrl(url.getText().toString());String t=token.getText().toString().trim();if(t.isEmpty()||t.contains("\n")||t.contains("\r"))throw new Exception("أدخل رمز وصول صالحًا.");prefs.edit().putString("server_url",u).putString("access_token",t).apply();toast("تم حفظ الإعدادات.");}
            catch(Exception ex){error(ex.getMessage());}
        }));
        card.addView(button("مسح رابط الخدمة ورمز الوصول",false,()->{prefs.edit().remove("server_url").remove("access_token").apply();showSettings();toast("تم مسح إعدادات الخدمة.");}));
        card.addView(label("التسجيل وعينات الصوت والمحاضر محفوظة في مساحة التطبيق الخاصة. حذف بيانات التطبيق أو إزالته يمسحها. النسخ الاحتياطي التلقائي معطّل.",13,MUTED,false));
        card.addView(label("للمطور: يسمح النموذج بـ HTTP على 10.0.2.2 أو localhost فقط. على هاتف حقيقي استخدم خادمًا يدعم HTTPS.",12,MUTED,false));
        content.addView(card);
        LinearLayout about=card();about.addView(label("مجلس · نموذج أولي 0.1",18,INK,true));
        about.addView(label("فصل المتحدثين ليس إثباتًا للهوية. راجع الأسماء والنص والقرارات قبل اعتماد المحضر. التعرف السحابي يحتاج خادمًا ومفتاح خدمة صالحين.",14,MUTED,false));content.addView(about);
    }

    private void showHistory() {
        screen="history";shell("محاضر محفوظة على جهازك");
        content.addView(label("اجتماعاتك",25,INK,true));JSONArray meetings=store.meetings();
        if(meetings.length()==0){LinearLayout empty=card();empty.addView(label("أول محضر يبدأ بحديث",20,INK,true));empty.addView(label("سجّل اجتماعًا وحلله، أو جرّب المثال الجاهز للتعرف على شكل المحضر.",14,MUTED,false));empty.addView(button("عرض اجتماع تجريبي",true,()->showDemo()));content.addView(empty);}
        for(int i=0;i<meetings.length();i++){
            JSONObject meeting=meetings.optJSONObject(i);if(meeting==null)continue;LinearLayout row=card();
            row.addView(label(meeting.optString("title","اجتماع"),20,INK,true));
            row.addView(label((meeting.optBoolean("_demo")?"مثال تجريبي • ":"")+date(meeting.optLong("_saved_at"))+" • "+time(meeting.optDouble("duration_seconds")),12,MUTED,false));
            JSONObject minutes=meeting.optJSONObject("minutes");if(minutes!=null)row.addView(label(minutes.optString("summary"),14,MUTED,false));
            row.addView(button("فتح المحضر",true,()->{result=meeting;resultTab="minutes";showResult();}));
            row.addView(button("حذف الاجتماع",false,()->new AlertDialog.Builder(this).setTitle("حذف الاجتماع؟").setMessage("سيُحذف المحضر والتسجيل المرتبط به من هذا الجهاز.").setNegativeButton("إلغاء",null).setPositiveButton("حذف",(d,w)->{try{store.deleteMeeting(meeting);showHistory();}catch(Exception ex){error(ex.getMessage());}}).show()));content.addView(row);
        }
    }

    private void showResult() {
        if(result==null){showHistory();return;}screen="result";shell("محضر الاجتماع • مسودة للمراجعة");
        LinearLayout head=card();head.addView(label(result.optString("title"),24,INK,true));
        head.addView(label(time(result.optDouble("duration_seconds"))+" • "+result.optJSONArray("speakers").length()+" متحدثين"+(result.optBoolean("_demo")?" • محتوى تجريبي":""),13,MUTED,false));
        head.addView(label("اضغط على اسم متحدث لتصحيحه. راجع المحضر قبل مشاركته.",13,MUTED,false));
        JSONArray speakers=result.optJSONArray("speakers");
        for(int i=0;i<speakers.length();i++){JSONObject speaker=speakers.optJSONObject(i);head.addView(button(speaker.optString("name")+(speaker.optBoolean("matched_reference")?" · عينة مسماة":" · تسمية قابلة للتعديل"),false,()->renameSpeaker(speaker)));}
        LinearLayout tabs=new LinearLayout(this);Button minutes=button("المحضر والملخص",resultTab.equals("minutes"),()->{resultTab="minutes";showResult();});Button transcript=button("النص الكامل",resultTab.equals("transcript"),()->{resultTab="transcript";showResult();});
        tabs.addView(minutes,new LinearLayout.LayoutParams(0,-2,1));tabs.addView(transcript,new LinearLayout.LayoutParams(0,-2,1));head.addView(tabs);content.addView(head);
        if("minutes".equals(resultTab))renderMinutes();else renderTranscript();
        content.addView(button("مشاركة المحضر كنص",true,()->{Intent share=new Intent(Intent.ACTION_SEND);share.setType("text/plain");share.putExtra(Intent.EXTRA_SUBJECT,result.optString("title"));share.putExtra(Intent.EXTRA_TEXT,store.report(result));startActivity(Intent.createChooser(share,"مشاركة المحضر"));}));
        if(result.optBoolean("_demo")&&!result.has("_id"))content.addView(button("حفظ المثال التجريبي",false,()->{try{store.saveMeeting(result);toast("تم حفظ المثال في المحاضر.");showResult();}catch(Exception ex){error(ex.getMessage());}}));
    }

    private void renderMinutes() {
        JSONObject m=result.optJSONObject("minutes");if(m==null)return;
        section("الملخص",m.optString("summary","لم يتوفر ملخص."));
        listSection("نقاط النقاش",m.optJSONArray("discussion_points"),"لا توجد نقاط مستخرجة.");
        JSONArray decisions=m.optJSONArray("decisions");LinearLayout dc=card();dc.addView(label("القرارات",20,INK,true));
        if(decisions==null||decisions.length()==0)dc.addView(label("لم يُذكر قرار واضح في النص.",14,MUTED,false));
        else for(int i=0;i<decisions.length();i++){JSONObject item=decisions.optJSONObject(i);if(item!=null){dc.addView(label("• "+item.optString("text"),15,INK,false));evidence(dc,item.optJSONArray("segment_ids"));}}content.addView(dc);
        JSONArray actions=m.optJSONArray("action_items");LinearLayout ac=card();ac.addView(label("المهام والمتابعة",20,INK,true));
        if(actions==null||actions.length()==0)ac.addView(label("لم تُذكر مهام واضحة في النص.",14,MUTED,false));
        else for(int i=0;i<actions.length();i++){JSONObject item=actions.optJSONObject(i);if(item==null)continue;ac.addView(label("• "+item.optString("task"),15,INK,true));ac.addView(label("المسؤول: "+displayValue(item,"owner")+"  |  الموعد: "+displayValue(item,"due_date"),13,MUTED,false));evidence(ac,item.optJSONArray("segment_ids"));}content.addView(ac);
        listSection("أسئلة مفتوحة",m.optJSONArray("open_questions"),"لم تُستخرج أسئلة مفتوحة.");
    }

    private String displayValue(JSONObject o,String key){return o.isNull(key)||o.optString(key).isEmpty()?"غير مذكور":o.optString(key);}
    private void evidence(LinearLayout parent,JSONArray ids){if(ids==null||ids.length()==0)return;Button b=button("عرض الكلام الذي استند إليه هذا البند",false,()->{StringBuilder text=new StringBuilder();JSONArray segments=result.optJSONArray("segments");for(int i=0;i<ids.length();i++){String id=ids.optString(i);for(int j=0;j<segments.length();j++){JSONObject seg=segments.optJSONObject(j);if(id.equals(seg.optString("id")))text.append(speakerName(seg.optString("speaker_id"))).append(" · ").append(time(seg.optDouble("start"))).append("\n").append(seg.optString("text")).append("\n\n");}}new AlertDialog.Builder(this).setTitle("مرجع من النص").setMessage(text.length()>0?text.toString():"المرجع غير متوفر؛ راجع النص الكامل.").setPositiveButton("إغلاق",null).show();});b.setTextSize(12);parent.addView(b);}
    private void renderTranscript(){JSONArray segments=result.optJSONArray("segments");for(int i=0;i<segments.length();i++){JSONObject seg=segments.optJSONObject(i);if(seg==null)continue;LinearLayout row=card();row.addView(label(speakerName(seg.optString("speaker_id"))+" · "+time(seg.optDouble("start"))+" – "+time(seg.optDouble("end")),13,TEAL,true));row.addView(label(seg.optString("text"),16,INK,false));content.addView(row);}}
    private String speakerName(String id){JSONArray speakers=result.optJSONArray("speakers");for(int i=0;i<speakers.length();i++){JSONObject s=speakers.optJSONObject(i);if(id.equals(s.optString("id")))return s.optString("name");}return "متحدث";}
    private void renameSpeaker(JSONObject speaker){
        EditText input=edit("اسم المتحدث",speaker.optString("name"),false);
        new AlertDialog.Builder(this).setTitle("تصحيح اسم المتحدث").setView(input)
            .setNegativeButton("إلغاء",null).setPositiveButton("حفظ",(d,w)->{
                String n=input.getText().toString().trim();
                if(n.isEmpty()||n.length()>64){toast("أدخل اسمًا من 1 إلى 64 حرفًا.");return;}
                try{
                    JSONObject updated=new JSONObject(result.toString());
                    String oldName=speaker.optString("name"),id=speaker.optString("id");
                    JSONArray people=updated.getJSONArray("speakers");
                    for(int i=0;i<people.length();i++)if(id.equals(people.getJSONObject(i).getString("id")))people.getJSONObject(i).put("name",n);
                    JSONArray tasks=updated.getJSONObject("minutes").getJSONArray("action_items");
                    for(int i=0;i<tasks.length();i++){JSONObject task=tasks.getJSONObject(i);if(oldName.equals(task.optString("owner")))task.put("owner",n);}
                    if(updated.has("_id"))store.saveMeeting(updated);
                    result=updated;showResult();
                }catch(Exception ex){error("تعذر حفظ الاسم.");}
            }).show();
    }

    private void showDemo() {
        try {
            result=new JSONObject("{\"title\":\"مثال تجريبي: إطلاق المنتج\",\"language\":\"ar\",\"duration_seconds\":92,\"_demo\":true,\"speakers\":[{\"id\":\"A\",\"name\":\"أحمد\",\"matched_reference\":false},{\"id\":\"B\",\"name\":\"سارة\",\"matched_reference\":false},{\"id\":\"C\",\"name\":\"خالد\",\"matched_reference\":false}],\"segments\":[{\"id\":\"s1\",\"speaker_id\":\"A\",\"start\":0,\"end\":14,\"text\":\"نراجع اليوم جاهزية إطلاق المنتج. نحتاج إلى اختبار النسخة قبل موعد الإطلاق.\"},{\"id\":\"s2\",\"speaker_id\":\"B\",\"start\":15,\"end\":32,\"text\":\"سأتولى اختبار النسخة وإرسال تقرير الأخطاء يوم الخميس.\"},{\"id\":\"s3\",\"speaker_id\":\"A\",\"start\":33,\"end\":48,\"text\":\"متفقون على إطلاق نسخة تجريبية محدودة بعد مراجعة تقرير الاختبار.\"},{\"id\":\"s4\",\"speaker_id\":\"C\",\"start\":60,\"end\":76,\"text\":\"انضممت الآن. سأجهز صفحة التعريف بالمنتج، ولم أحدد موعد تسليمها بعد.\"},{\"id\":\"s5\",\"speaker_id\":\"B\",\"start\":77,\"end\":92,\"text\":\"يبقى تحديد عدد المشاركين في التجربة. كم مستخدمًا نستهدف؟\"}],\"minutes\":{\"summary\":\"ناقش الفريق جاهزية المنتج، واتفق على إطلاق تجربة محدودة بعد مراجعة نتائج الاختبار. تولت سارة الاختبار، وتولى خالد صفحة التعريف. بقي حجم التجربة دون تحديد.\",\"discussion_points\":[\"جاهزية المنتج واختبار النسخة\",\"إطلاق تجربة محدودة\",\"إعداد صفحة التعريف وحجم التجربة\"],\"decisions\":[{\"text\":\"إطلاق نسخة تجريبية محدودة بعد مراجعة تقرير الاختبار.\",\"segment_ids\":[\"s3\"]}],\"action_items\":[{\"task\":\"اختبار النسخة وإرسال تقرير الأخطاء\",\"owner\":\"سارة\",\"due_date\":\"الخميس، كما ورد في الحديث\",\"segment_ids\":[\"s2\"]},{\"task\":\"تجهيز صفحة التعريف بالمنتج\",\"owner\":\"خالد\",\"due_date\":null,\"segment_ids\":[\"s4\"]}],\"open_questions\":[\"كم مستخدمًا سيشارك في النسخة التجريبية؟\"]}}");
            resultTab="minutes";showResult();
        } catch(Exception ex){error("تعذر فتح المثال التجريبي.");}
    }

    private void play(File file){if(RecordingService.isRecording()||sampleRecorder!=null){toast("أوقف التسجيل قبل الاستماع.");return;}stopPlayer();if(!file.exists()){toast("ملف التسجيل غير موجود.");return;}try{player=new MediaPlayer();player.setDataSource(file.getAbsolutePath());player.prepare();player.setOnCompletionListener(p->stopPlayer());player.start();toast("جارٍ التشغيل؛ مغادرة الشاشة توقفه.");}catch(Exception ex){stopPlayer();error("تعذر تشغيل التسجيل.");}}
    private void stopPlayer(){if(player!=null){try{player.release();}catch(Exception ignored){}player=null;}}
    private void section(String title,String text){LinearLayout card=card();card.addView(label(title,20,INK,true));card.addView(label(text,16,INK,false));content.addView(card);}
    private void listSection(String title,JSONArray items,String empty){LinearLayout card=card();card.addView(label(title,20,INK,true));if(items==null||items.length()==0)card.addView(label(empty,14,MUTED,false));else for(int i=0;i<items.length();i++)card.addView(label("• "+items.optString(i),15,INK,false));content.addView(card);}
    private LinearLayout card(){LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);l.setPadding(dp(18),dp(18),dp(18),dp(18));GradientDrawable bg=new GradientDrawable();bg.setColor(Color.WHITE);bg.setCornerRadius(dp(18));l.setBackground(bg);LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,0,0,dp(14));l.setLayoutParams(p);return l;}
    private TextView label(String text,int size,int color,boolean bold){TextView v=new TextView(this);v.setText(text);v.setTextSize(size);v.setTextColor(color);v.setLineSpacing(dp(4),1);v.setPadding(0,dp(5),0,dp(5));if(bold)v.setTypeface(Typeface.DEFAULT,Typeface.BOLD);return v;}
    private EditText edit(String hint,String value,boolean password){EditText e=new EditText(this);e.setHint(hint);e.setText(value);e.setTextSize(15);e.setTextColor(INK);e.setSingleLine(true);e.setPadding(dp(10),dp(12),dp(10),dp(12));e.setInputType(InputType.TYPE_CLASS_TEXT|(password?InputType.TYPE_TEXT_VARIATION_PASSWORD:0));return e;}
    private Button button(String text,boolean primary,Runnable action){Button b=new Button(this);b.setText(text);b.setAllCaps(false);b.setTextSize(14);b.setTextColor(primary?Color.WHITE:TEAL);GradientDrawable bg=new GradientDrawable();bg.setColor(primary?TEAL:Color.rgb(235,245,242));bg.setCornerRadius(dp(12));b.setBackground(bg);b.setPadding(dp(12),dp(6),dp(12),dp(6));LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(48));p.setMargins(0,dp(10),0,0);b.setLayoutParams(p);b.setOnClickListener(v->{if("meeting".equals(screen)&&titleField!=null)prefs.edit().putString("last_title",titleField.getText().toString()).apply();stopPlayer();action.run();});return b;}
    private int dp(int n){return (int)(getResources().getDisplayMetrics().density*n+0.5f);}
    private String time(double seconds){long s=Math.max(0,(long)seconds);return String.format(Locale.US,"%02d:%02d",s/60,s%60);}
    private String date(long millis){return new SimpleDateFormat("yyyy/MM/dd HH:mm",Locale.US).format(new Date(millis));}
    private void toast(String message){Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    private void error(String message){if(isFinishing()||isDestroyed())return;new AlertDialog.Builder(this).setTitle("لم تكتمل العملية").setMessage(message==null||message.isEmpty()?"حدث خطأ. حاول مجددًا.":message).setPositiveButton("حسنًا",null).show();}
    @Override public void onBackPressed(){if("result".equals(screen)){showHistory();}else if(!"meeting".equals(screen)){showMeeting();}else super.onBackPressed();}
}
