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
import android.net.Uri;
import android.database.Cursor;
import android.provider.OpenableColumns;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.Editable;
import android.text.TextWatcher;
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
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.ArrayAdapter;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A real recorder and an explicitly configured remote analysis client. */
public class MainActivity extends Activity {
    private static final String ANALYSIS_UPDATE="com.majlis.app.ANALYSIS_UPDATE";
    private static volatile boolean analysisRunning;
    private static volatile boolean importRunning;
    private static final int IMPORT_AUDIO=31, EXPORT_REPORT=32;
    private static final String[] TRANSLATION_CODES={"en","fr","de","es","tr","he","ru","el","uk","zh","fa","ur"};
    private static final String[] TRANSLATION_NAMES={"الإنجليزية","الفرنسية","الألمانية","الإسبانية","التركية","العبرية","الروسية","اليونانية","الأوكرانية","الصينية","الفارسية","الأوردو"};
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
    private Button recordButton, pauseButton, analyzeButton;
    private String historyQuery="", transcriptQuery="", exportKind, exportSnapshot;
    private SeekBar playbackSeek;
    private TextView playbackTime;
    private Button playbackButton;
    private Runnable playbackTick;
    private String screen = "meeting";
    private boolean busy;
    private Runnable afterPermission;
    private JSONObject result;
    private String resultTab = "minutes";
    private String translationLanguage="en";
    private MediaRecorder sampleRecorder;
    private MediaPlayer player;
    private File sampleFile;
    private AlertDialog sampleDialog;
    private long sampleStarted;
    private Runnable sampleTick;
    private AlertDialog fixtureDialog;

    private final BroadcastReceiver recorderUpdates = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent intent) {
            if(ANALYSIS_UPDATE.equals(intent.getAction())) {
                busy=analysisRunning||importRunning;
                if("meeting".equals(screen))showMeeting();
                else if("voices".equals(screen))showVoices();
                else if("history".equals(screen))showHistory();
                else if("result".equals(screen)&&result!=null){
                    if(result.has("_id")){JSONArray saved=store.meetings();for(int i=0;i<saved.length();i++){JSONObject meeting=saved.optJSONObject(i);if(meeting!=null&&meeting.optString("_id").equals(result.optString("_id")))result=meeting;}}
                    showResult();
                }
                if(intent.hasExtra("error"))error(intent.getStringExtra("error"));
                else if(intent.getBooleanExtra("saved",false))toast("تم حفظ المحضر في تبويب المحاضر.");
                return;
            }
            String state = intent.getStringExtra("state");
            if ("recording".equals(state)||"paused".equals(state)) {
                updateRecording(intent.getLongExtra("elapsed_ms",0), intent.getIntExtra("amplitude",0));
            } else if ("stopped".equals(state)) {
                String path = intent.getStringExtra("path");
                if (path != null) prefs.edit().putString("last_audio",path).apply();
                if ("duration_limit".equals(intent.getStringExtra("reason"))) toast("اكتمل الحد الأقصى: 60 دقيقة.");
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
        if(state!=null){
            exportKind=state.getString("export_kind");
            if(exportKind!=null)try{File pending=new File(getCacheDir(),"pending-export.json");if(pending.isFile()&&pending.length()<=4*1024*1024)exportSnapshot=new String(java.nio.file.Files.readAllBytes(pending.toPath()),StandardCharsets.UTF_8);}catch(Exception ignored){}
            resultTab=state.getString("result_tab","minutes");
            translationLanguage=state.getString("translation_language","en");
            try{String saved=state.getString("result");if(saved!=null)result=new JSONObject(saved);
                else{String id=state.getString("result_id");if(id!=null){JSONArray meetings=store.meetings();for(int i=0;i<meetings.length();i++)if(id.equals(meetings.getJSONObject(i).optString("_id")))result=meetings.getJSONObject(i);}}
            }catch(Exception ignored){}
            String previous=state.getString("screen","meeting");
            if("result".equals(previous)&&result!=null){showResult();return;}
            if("history".equals(previous)){showHistory();return;}
            if("settings".equals(previous)){showSettings();return;}
        }
        showMeeting();
    }

    @Override public void onSaveInstanceState(Bundle state){
        super.onSaveInstanceState(state);state.putString("screen",screen);state.putString("result_tab",resultTab);
        state.putString("translation_language",translationLanguage);
        if(result!=null){if(result.has("_id"))state.putString("result_id",result.optString("_id"));else if(result.toString().length()<80_000)state.putString("result",result.toString());}
        state.putString("export_kind",exportKind);
    }

    @Override public void onResume() {
        super.onResume();
        if ("meeting".equals(screen)) showMeeting();
    }

    @Override public void onPause() {
        if (titleField != null && "meeting".equals(screen)) prefs.edit().putString("last_title",titleField.getText().toString()).apply();
        finishSample(false,null);
        if(fixtureDialog!=null){fixtureDialog.dismiss();fixtureDialog=null;}
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
        stopPlayer();playbackSeek=null;playbackTime=null;playbackButton=null;
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
        busy=analysisRunning||importRunning;
        screen="meeting"; shell("من حديث الاجتماع إلى محضر قابل للمراجعة");
        LinearLayout card = card();
        card.addView(label("اجتماع جديد",23,INK,true));
        card.addView(label("كلام متعدد اللغات • فصل المتحدثين • محضر عربي",13,MUTED,false));
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
        pauseButton=button(RecordingService.isPaused()?"متابعة التسجيل":"إيقاف مؤقت",false,()->{
            if(RecordingService.isRecording())startService(new Intent(this,RecordingService.class).setAction(RecordingService.isPaused()?RecordingService.ACTION_RESUME:RecordingService.ACTION_PAUSE));
        });
        pauseButton.setVisibility(RecordingService.isRecording()?View.VISIBLE:View.GONE);card.addView(pauseButton);
        File last=lastAudio();
        if (!RecordingService.isRecording() && last != null) {
            status.setText("التسجيل محفوظ على الجهاز • جاهز للتحليل");
            addPlayback(card,last);
        }
        Button imported=button("استيراد ملف صوتي",false,()->chooseAudio());
        imported.setEnabled(!RecordingService.isRecording()&&!busy);card.addView(imported);
        analyzeButton=button(importRunning?"جارٍ استيراد الملف…":busy?"جارٍ تحليل الاجتماع…":"إعداد المحضر والملخص",true,()->analyze());
        analyzeButton.setEnabled(!RecordingService.isRecording() && last != null && !busy); card.addView(analyzeButton);
        if (busy) { ProgressBar progress=new ProgressBar(this); card.addView(progress); }
        card.addView(label("حتى 60 دقيقة فعلية، مع إيقاف مؤقت ومتابعة. يمكنك استيراد M4A أو MP3 أو WAV حتى 24 مليون بايت؛ يتحقق الخادم من المدة والصيغة. يبقى الصوت محليًا حتى توافق على إرساله.",12,MUTED,false));
        if(prefs.getBoolean("test_mode",false)){
            card.addView(label("وضع الاختبار: "+prefs.getString("test_notes",""),14,TEAL,true));
            card.addView(label("بعد التحليل استمع وصحح النص وإسناد المتحدثين، ثم احسب أخطاء النسخة الأصلية مقارنة بمرجعك. النتائج مرتبطة بهذا الهاتف وهذه الظروف.",12,MUTED,false));
            Button cancelTest=button("إلغاء وضع الاختبار",false,()->{prefs.edit().remove("test_mode").remove("test_notes").apply();showMeeting();});cancelTest.setEnabled(!RecordingService.isRecording()&&!busy);card.addView(cancelTest);
        }else{
            Button test=button("تجربة قياس موثقة",false,()->configureTest());test.setEnabled(!RecordingService.isRecording()&&!busy);card.addView(test);
        }
        content.addView(card);
        LinearLayout info=card(); info.addView(label("لتمييز الأسماء",19,INK,true));
        JSONArray voices=store.profiles();
        info.addView(label(voices.length()==0?"أضف عينة قصيرة باسم كل مشارك من تبويب الأصوات، أو سمِّ المتحدثين بعد التحليل.":"لديك "+voices.length()+" عينات صوت مسماة ستُستخدم عند التحليل.",14,MUTED,false));
        info.addView(label("من ينضم لاحقًا يظهر بعد أن يتكلم بصوت واضح. تمييز المتحدثين يجري بعد إنهاء التسجيل، وقد يحتاج تصحيحًا عند التداخل.",14,MUTED,false));
        info.addView(label("للأصوات البعيدة ضع الهاتف وسط الطاولة، أو استخدم ميكروفون اجتماعات. التطبيق لا يضمن التقاط كلام لا يصل بوضوح إلى الميكروفون.",14,MUTED,false));
        content.addView(info);
        Button demo=button("تجربة اجتماع بمحتوى تجريبي",false,()->showDemo()); demo.setEnabled(!busy); content.addView(demo);
        Button fixtures=button("مختبر المتحدثين واللغات والضوضاء",false,()->showFixtures());fixtures.setEnabled(!busy&&!RecordingService.isRecording());content.addView(fixtures);
        updateRecording(RecordingService.elapsedMillis(),0);
    }

    private void updateRecording(long elapsed,int amplitude) {
        if (!"meeting".equals(screen) || timer==null) return;
        if (RecordingService.isRecording()) {
            boolean paused=RecordingService.isPaused();
            setTextIfChanged(timer,time(elapsed/1000.0));setTextIfChanged(status,paused?"التسجيل متوقف مؤقتًا • الوقت ثابت":"● التسجيل جارٍ — يمكن قفل الشاشة"); status.setTextColor(TEAL);
            pauseButton.setVisibility(View.VISIBLE);setTextIfChanged(pauseButton,paused?"متابعة التسجيل":"إيقاف مؤقت");
            setTextIfChanged(recordButton,"إنهاء التسجيل"); recordButton.setEnabled(!busy); analyzeButton.setEnabled(false);
            consent.setEnabled(false); titleField.setEnabled(false); level.setProgress(paused?0:amplitude);
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

    private void chooseAudio(){
        if(RecordingService.isRecording()||analysisRunning||importRunning)return;
        Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("audio/*");
        pick.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"audio/mp4","audio/x-m4a","audio/mpeg","audio/wav","audio/x-wav"});
        try{startActivityForResult(pick,IMPORT_AUDIO);}catch(Exception ex){error("تعذر فتح منتقي الملفات.");}
    }

    @Override protected void onActivityResult(int code,int outcome,Intent data){
        super.onActivityResult(code,outcome,data);
        if(outcome!=RESULT_OK||data==null||data.getData()==null){if(code==EXPORT_REPORT){exportKind=null;exportSnapshot=null;new File(getCacheDir(),"pending-export.json").delete();}return;}
        Uri uri=data.getData();
        if(code==IMPORT_AUDIO){
            if(RecordingService.isRecording()||analysisRunning||importRunning){toast("انتظر انتهاء العملية الحالية.");return;}
            String name="";long size=-1;
            try(Cursor c=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE},null,null,null)){
                if(c!=null&&c.moveToFirst()){name=c.getString(0);if(!c.isNull(1))size=c.getLong(1);}
            }catch(Exception ex){error("تعذر قراءة معلومات الملف.");return;}
            if(name==null)name="";
            String lower=name.toLowerCase(Locale.ROOT);String extension=lower.endsWith(".mp3")?"mp3":lower.endsWith(".wav")?"wav":lower.endsWith(".m4a")?"m4a":null;
            if(extension==null){error("اختر ملف M4A أو MP3 أو WAV.");return;}
            if(size>24_000_000L){error("حجم الملف يتجاوز الحد: 24 مليون بايت.");return;}
            final String importedName=name, importedExtension=extension;
            Runnable copy=()->importAudio(uri,importedName,importedExtension);
            if(lastAudio()!=null)new AlertDialog.Builder(this).setTitle("استبدال التسجيل المعلق؟")
                .setMessage("بعد نجاح الاستيراد سيُحذف التسجيل السابق الذي لم يُحلل. لا يُرفع أي ملف في هذه الخطوة.")
                .setNegativeButton("رجوع",null).setPositiveButton("استبدال واستيراد",(d,w)->copy.run()).show();
            else copy.run();
        }else if(code==EXPORT_REPORT&&exportSnapshot!=null&&exportKind!=null){
            final String snapshot=exportSnapshot,kind=exportKind;exportSnapshot=null;exportKind=null;
            new File(getCacheDir(),"pending-export.json").delete();
            worker.execute(()->{
                try(OutputStream out=getContentResolver().openOutputStream(uri,"wt")){
                    if(out==null)throw new Exception("تعذر فتح ملف التصدير.");
                    JSONObject meeting=new JSONObject(snapshot);
                    if("PDF".equals(kind))MeetingPdf.write(getApplicationContext(),exportText(meeting),out);
                    else{String text="JSON".equals(kind)?publicMeeting(meeting).toString(2):exportText(meeting);out.write(text.getBytes(StandardCharsets.UTF_8));}
                    handler.post(()->toast("تم تصدير المحضر."));
                }catch(Exception ex){handler.post(()->error("تعذر تصدير المحضر: "+ex.getMessage()));}
            });
        }
    }

    private void importAudio(Uri uri,String name,String extension){
        if(RecordingService.isRecording()||analysisRunning||importRunning)return;
        importRunning=true;busy=true;File old=lastAudio();showMeeting();status.setText("جارٍ نسخ الملف إلى مساحة التطبيق…");
        worker.execute(()->{
            File imported=new File(store.recordingsDir(),"import_"+System.currentTimeMillis()+"."+extension);
            try(InputStream in=getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(imported)){
                if(in==null)throw new Exception("تعذر قراءة الملف.");
                byte[] buffer=new byte[32_768];long count=0;int n;
                while((n=in.read(buffer))!=-1){count+=n;if(count>24_000_000L)throw new Exception("يتجاوز الملف 24 مليون بايت.");out.write(buffer,0,n);}
                if(count==0)throw new Exception("الملف فارغ.");
                out.flush();
                String title=name.substring(0,name.length()-extension.length()-1).trim();if(title.isEmpty())title="اجتماع مستورد";
                if(!prefs.edit().putString("last_audio",imported.getAbsolutePath()).putString("last_title",title.substring(0,Math.min(title.length(),120))).putBoolean("recording_incomplete",false).commit())throw new Exception("تعذر حفظ بيانات الملف.");
                if(old!=null&&!old.equals(imported))old.delete();
                handler.post(()->{importRunning=false;busy=analysisRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()));if(!isDestroyed()&&!isFinishing()){showMeeting();toast("حُفظ الملف محليًا. راجع العنوان ثم وافق على التحليل.");}});
            }catch(Exception ex){imported.delete();handler.post(()->{importRunning=false;busy=analysisRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("error",ex.getMessage()));});}
        });
    }

    private JSONObject publicMeeting(JSONObject meeting)throws Exception{
        JSONObject exported=new JSONObject();
        for(String key:new String[]{"title","language","duration_seconds","speakers","segments","minutes"})exported.put(key,meeting.get(key));
        exported.put("minutes_need_review",meeting.optBoolean("_minutes_stale"));
        if(meeting.has("_evaluation")){exported.put("evaluation",meeting.getJSONObject("_evaluation"));if(meeting.has("_baseline_segments"))exported.put("baseline_segments",meeting.getJSONArray("_baseline_segments"));}
        if(meeting.has("_speech_flags"))exported.put("speech_flags_reviewed_by_user",meeting.getJSONObject("_speech_flags"));
        if(meeting.has("_code_notes"))exported.put("text_code_notes_reviewed_by_user",meeting.getJSONObject("_code_notes"));
        if(meeting.has("_fixture"))exported.put("synthetic_fixture",meeting.getJSONObject("_fixture"));
        if(meeting.has("_translations")){exported.put("translations",meeting.getJSONObject("_translations"));exported.put("translations_need_review",meeting.optBoolean("_translations_stale"));}
        exported.put("demo",meeting.optBoolean("_demo"));return exported;
    }

    private void exportReport(){
        new AlertDialog.Builder(this).setTitle("تصدير المحضر").setItems(new String[]{"PDF","JSON","نص"},(d,which)->{
            exportKind=new String[]{"PDF","JSON","نص"}[which];exportSnapshot=result.toString();
            try{java.nio.file.Files.write(new File(getCacheDir(),"pending-export.json").toPath(),exportSnapshot.getBytes(StandardCharsets.UTF_8));}
            catch(Exception ex){exportKind=null;exportSnapshot=null;error("تعذر تجهيز ملف التصدير.");return;}
            String extension=which==0?"pdf":which==1?"json":"txt";
            Intent create=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(which==0?"application/pdf":which==1?"application/json":"text/plain");
            create.putExtra(Intent.EXTRA_TITLE,"majlis-"+new SimpleDateFormat("yyyyMMdd-HHmm",Locale.US).format(new Date())+"."+extension);
            try{startActivityForResult(create,EXPORT_REPORT);}catch(Exception ex){exportKind=null;exportSnapshot=null;new File(getCacheDir(),"pending-export.json").delete();error("تعذر فتح مكان الحفظ.");}
        }).show();
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
        final String testNotes=prefs.getBoolean("test_mode",false)?prefs.getString("test_notes",""):null;
        new AlertDialog.Builder(this).setTitle("إرسال الاجتماع للتحليل؟")
            .setMessage("سيُرسل تسجيل الاجتماع و"+profiles.length()+" عينات صوت مسماة إلى:\n"+url+"\nوسيستخدم الخادم خدمة OpenAI لتفريغ الكلام وتمييز المتحدثين وإعداد الملخص. قد تُحتسب تكلفة على حساب صاحب الخادم. يبقى المحضر قابلًا للمراجعة.")
            .setNegativeButton("إلغاء",null).setPositiveButton("موافق، أرسل للتحليل",(d,w)->{
                if(analysisRunning)return;
                analysisRunning=true;busy=true;prefs.edit().putString("last_title",title).apply();showMeeting();
                worker.execute(()->{
                    try {
                        long started=android.os.SystemClock.elapsedRealtime();
                        JSONObject analyzed=MeetingApi.analyze(url,token,audio,title,profiles);
                        analyzed.put("_audio_path",audio.getAbsolutePath()); analyzed.put("_demo",false);
                        analyzed.put("_baseline_segments",new JSONArray(analyzed.getJSONArray("segments").toString()));
                        analyzed.put("_analysis_seconds",(android.os.SystemClock.elapsedRealtime()-started)/1000.0);
                        analyzed.put("_audio_bytes",audio.length());analyzed.put("_device",Build.MANUFACTURER+" "+Build.MODEL);
                        analyzed.put("_capture_kind",audio.getName().startsWith("import_")?"ملف مستورد":"ميكروفون الهاتف");
                        try{JSONObject signal=new JSONObject(prefs.getString("last_signal","{}"));if(audio.getAbsolutePath().equals(signal.optString("path"))){signal.remove("path");analyzed.put("_signal",signal);}}catch(Exception ignored){}
                        if(testNotes!=null)analyzed.put("_test_notes",testNotes);
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
        busy=analysisRunning||importRunning;
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
        if(RecordingService.isRecording()||analysisRunning||importRunning||store.profiles().length()>=4)return;
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
        if(analysisRunning||importRunning){toast("انتظر انتهاء المعالجة قبل حذف العينة.");return;}
        new AlertDialog.Builder(this).setTitle("حذف عينة "+profile.optString("name")+"؟")
            .setMessage("سيُحذف تسجيل العينة من هذا الجهاز.").setNegativeButton("إلغاء",null).setPositiveButton("حذف",(d,w)->{
                if(analysisRunning||importRunning){toast("انتظر انتهاء المعالجة قبل حذف العينة.");return;}
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
        TextView connection=label("فحص الاتصال لا يرسل تسجيلًا أو عينات صوت.",13,MUTED,false);card.addView(connection);
        Button check=button("اختبار الاتصال",false,()->{});
        check.setOnClickListener(v->{
            final String address=url.getText().toString(),access=token.getText().toString();check.setEnabled(false);connection.setText("جارٍ فحص الخادم…");
            worker.execute(()->{try{JSONObject readiness=MeetingApi.status(address,access);handler.post(()->{check.setEnabled(true);connection.setText("الخادم متصل · إصدار "+readiness.optString("version")+" · حتى "+readiness.optInt("max_duration_seconds")/60+" دقيقة. هذا الفحص لا يختبر دقة التحليل أو رصيد المزود.");});}
                catch(Exception ex){handler.post(()->{check.setEnabled(true);connection.setText("تعذر الاتصال. تحقق من الرابط ورمز الوصول.");error(ex.getMessage());});}});
        });card.addView(check);
        card.addView(button("مسح رابط الخدمة ورمز الوصول",false,()->{prefs.edit().remove("server_url").remove("access_token").apply();showSettings();toast("تم مسح إعدادات الخدمة.");}));
        card.addView(label("التسجيل وعينات الصوت والمحاضر محفوظة في مساحة التطبيق الخاصة. حذف بيانات التطبيق أو إزالته يمسحها. النسخ الاحتياطي التلقائي معطّل.",13,MUTED,false));
        card.addView(label("للمطور: يسمح النموذج بـ HTTP على 10.0.2.2 أو localhost فقط. على هاتف حقيقي استخدم خادمًا يدعم HTTPS.",12,MUTED,false));
        content.addView(card);
        LinearLayout about=card();about.addView(label("مجلس · نموذج أولي 0.3",18,INK,true));
        about.addView(label("فصل المتحدثين ليس إثباتًا للهوية. راجع الأسماء والنص والقرارات قبل اعتماد المحضر. التعرف السحابي يحتاج خادمًا ومفتاح خدمة صالحين.",14,MUTED,false));content.addView(about);
    }

    private void showHistory() {
        screen="history";shell("محاضر محفوظة على جهازك");
        content.addView(label("اجتماعاتك",25,INK,true));
        EditText query=edit("البحث في المحاضر",historyQuery,false);content.addView(query);
        LinearLayout rows=new LinearLayout(this);rows.setOrientation(LinearLayout.VERTICAL);content.addView(rows);
        populateHistory(rows,historyQuery);
        query.addTextChangedListener(watcher(value->{historyQuery=value;populateHistory(rows,value);}));
    }

    private void populateHistory(LinearLayout rows,String query){
        rows.removeAllViews();JSONArray meetings=store.searchMeetings(query);
        if(meetings.length()==0){LinearLayout empty=card();empty.addView(label(query.trim().isEmpty()?"أول محضر يبدأ بحديث":"لا توجد محاضر تطابق البحث",20,INK,true));empty.addView(label("سجّل اجتماعًا وحلله، أو جرّب المثال الجاهز للتعرف على شكل المحضر.",14,MUTED,false));empty.addView(button("عرض اجتماع تجريبي",true,()->showDemo()));rows.addView(empty);}
        for(int i=0;i<meetings.length();i++){
            JSONObject meeting=meetings.optJSONObject(i);if(meeting==null)continue;LinearLayout row=card();
            row.addView(label(meeting.optString("title","اجتماع"),20,INK,true));
            row.addView(label((meeting.optBoolean("_demo")?"مثال تجريبي • ":"")+date(meeting.optLong("_saved_at"))+" • "+time(meeting.optDouble("duration_seconds")),12,MUTED,false));
            JSONObject minutes=meeting.optJSONObject("minutes");if(minutes!=null)row.addView(label(minutes.optString("summary"),14,MUTED,false));
            row.addView(button("فتح المحضر",true,()->{result=meeting;resultTab="minutes";showResult();}));
            Button remove=button("حذف الاجتماع",false,()->new AlertDialog.Builder(this).setTitle("حذف الاجتماع؟").setMessage("سيُحذف المحضر والتسجيل المرتبط به من هذا الجهاز.").setNegativeButton("إلغاء",null).setPositiveButton("حذف",(d,w)->{
                if(analysisRunning||importRunning){toast("انتظر انتهاء المعالجة قبل حذف الاجتماع.");return;}
                try{store.deleteMeeting(meeting);showHistory();}catch(Exception ex){error(ex.getMessage());}
            }).show());remove.setEnabled(!analysisRunning&&!importRunning);row.addView(remove);rows.addView(row);
        }
    }

    private void showResult() {
        if(result==null){showHistory();return;}screen="result";shell("محضر الاجتماع • مسودة للمراجعة");
        LinearLayout head=card();head.addView(label(result.optString("title"),24,INK,true));
        head.addView(label(time(result.optDouble("duration_seconds"))+" • "+result.optJSONArray("speakers").length()+" متحدثين"+(result.optBoolean("_demo")?" • محتوى تجريبي":""),13,MUTED,false));
        head.addView(label("اضغط على اسم متحدث لتصحيحه. راجع المحضر قبل مشاركته.",13,MUTED,false));
        if(result.optJSONObject("_fixture")!=null){
            head.addView(label(result.optJSONObject("_fixture").optString("notice_ar","مرجع تجريبي مصطنع، وليس نتيجة تحليل صوت حقيقي."),13,MUTED,false));
            head.addView(button("محاكاة ظهور المتحدثين وعودتهم",false,()->simulateRoster()));
        }
        if(result.optBoolean("_minutes_stale"))head.addView(label("النص تغيّر؛ المحضر يحتاج إعادة تلخيص ومراجعة.",15,Color.rgb(161,94,12),true));
        JSONObject measured=result.optJSONObject("_evaluation");
        if(measured!=null)head.addView(label("اختبار بمرجع راجعته أنت · راجع نتائج الكلمات والأحرف وحدود القياس أدناه",13,TEAL,true));
        File audio=resultAudio();if(audio!=null)addPlayback(head,audio);
        JSONArray speakers=result.optJSONArray("speakers");
        for(int i=0;i<speakers.length();i++){JSONObject speaker=speakers.optJSONObject(i);head.addView(button(speaker.optString("name")+(speaker.optBoolean("matched_reference")?" · عينة مسماة":" · تسمية قابلة للتعديل"),false,()->renameSpeaker(speaker)));}
        LinearLayout tabs=new LinearLayout(this);Button minutes=button("المحضر والملخص",resultTab.equals("minutes"),()->{resultTab="minutes";showResult();});Button transcript=button("النص الكامل",resultTab.equals("transcript"),()->{resultTab="transcript";showResult();});
        tabs.addView(minutes,new LinearLayout.LayoutParams(0,-2,1));tabs.addView(transcript,new LinearLayout.LayoutParams(0,-2,1));head.addView(tabs);content.addView(head);
        if("translation".equals(resultTab))renderTranslation();else if("minutes".equals(resultTab))renderMinutes();else renderTranscript();
        content.addView(button("ترجمة اللقاء",false,()->chooseTranslation()));
        if(result.has("_baseline_segments")&&!result.optBoolean("_demo")){
            Button evaluate=button("قياس الدقة بعد المراجعة",false,()->evaluateMeeting());evaluate.setEnabled(!analysisRunning&&!importRunning);content.addView(evaluate);
        }
        if(measured!=null)renderEvaluation(measured);
        content.addView(button("تصدير المحضر",false,()->exportReport()));
        content.addView(button("مشاركة المحضر كنص",true,()->{Intent share=new Intent(Intent.ACTION_SEND);share.setType("text/plain");share.putExtra(Intent.EXTRA_SUBJECT,result.optString("title"));share.putExtra(Intent.EXTRA_TEXT,exportText(result));startActivity(Intent.createChooser(share,"مشاركة المحضر"));}));
        if(result.optBoolean("_demo")&&!result.has("_id"))content.addView(button("حفظ المثال التجريبي",false,()->{try{store.saveMeeting(result);toast("تم حفظ المثال في المحاضر.");showResult();}catch(Exception ex){error(ex.getMessage());}}));
    }

    private void renderMinutes() {
        JSONObject m=result.optJSONObject("minutes");if(m==null)return;
        section("الملخص",m.optString("summary","لم يتوفر ملخص."));
        content.addView(button("تعديل الملخص",false,()->editSummary()));
        Button summarize=button(analysisRunning?"جارٍ إعادة التلخيص…":"إعادة تلخيص النص المصحح",false,()->resummarize());
        summarize.setEnabled(!analysisRunning&&!importRunning&&!RecordingService.isRecording());content.addView(summarize);
        listSection("نقاط النقاش",m.optJSONArray("discussion_points"),"لا توجد نقاط مستخرجة.");
        JSONArray decisions=m.optJSONArray("decisions");LinearLayout dc=card();dc.addView(label("القرارات",20,INK,true));
        if(decisions==null||decisions.length()==0)dc.addView(label("لم يُذكر قرار واضح في النص.",14,MUTED,false));
        else for(int i=0;i<decisions.length();i++){JSONObject item=decisions.optJSONObject(i);if(item!=null){dc.addView(label("• "+item.optString("text"),15,INK,false));evidence(dc,item.optJSONArray("segment_ids"));}}content.addView(dc);
        JSONArray actions=m.optJSONArray("action_items");LinearLayout ac=card();ac.addView(label("المهام والمتابعة",20,INK,true));
        if(actions==null||actions.length()==0)ac.addView(label("لم تُذكر مهام واضحة في النص.",14,MUTED,false));
        else for(int i=0;i<actions.length();i++){JSONObject item=actions.optJSONObject(i);if(item==null)continue;ac.addView(label("• "+item.optString("task"),15,INK,true));ac.addView(label("المسؤول: "+displayValue(item,"owner")+"  |  الموعد: "+displayValue(item,"due_date"),13,MUTED,false));
            final int index=i;CheckBox done=new CheckBox(this);done.setText("تم إنجاز المهمة");done.setChecked(item.optBoolean("completed"));
            done.setEnabled(!analysisRunning);done.setOnCheckedChangeListener((view,checked)->{try{JSONObject next=new JSONObject(result.toString());next.getJSONObject("minutes").getJSONArray("action_items").getJSONObject(index).put("completed",checked);commitEdit(next,false);}catch(Exception ex){done.setOnCheckedChangeListener(null);done.setChecked(!checked);error("تعذر حفظ حالة المهمة.");}});ac.addView(done);
            evidence(ac,item.optJSONArray("segment_ids"));}content.addView(ac);
        listSection("أسئلة مفتوحة",m.optJSONArray("open_questions"),"لم تُستخرج أسئلة مفتوحة.");
    }

    private String displayValue(JSONObject o,String key){return o.isNull(key)||o.optString(key).isEmpty()?"غير مذكور":o.optString(key);}
    private void evidence(LinearLayout parent,JSONArray ids){
        if(ids==null||ids.length()==0)return;
        Button b=button("عرض الكلام الذي استند إليه هذا البند",false,()->{
            StringBuilder text=new StringBuilder();double start=-1;JSONArray segments=result.optJSONArray("segments");
            for(int i=0;i<ids.length();i++)for(int j=0;j<segments.length();j++){
                JSONObject seg=segments.optJSONObject(j);
                if(ids.optString(i).equals(seg.optString("id"))){
                    if(start<0)start=seg.optDouble("start");
                    text.append(speakerName(seg.optString("speaker_id"))).append(" · ").append(time(seg.optDouble("start"))).append("\n").append(seg.optString("text")).append("\n\n");
                }
            }
            AlertDialog.Builder dialog=new AlertDialog.Builder(this).setTitle("مرجع من النص").setMessage(text.length()>0?text.toString():"المرجع غير متوفر؛ راجع النص الكامل.").setPositiveButton("إغلاق",null);
            File audio=resultAudio();final double at=start;
            if(audio!=null&&at>=0)dialog.setNeutralButton("استماع لهذا المقطع",(d,w)->playAt(audio,at));
            dialog.show();
        });b.setTextSize(12);parent.addView(b);
    }

    private void renderTranscript(){
        EditText query=edit("البحث في النص",transcriptQuery,false);content.addView(query);
        LinearLayout rows=new LinearLayout(this);rows.setOrientation(LinearLayout.VERTICAL);content.addView(rows);
        populateTranscript(rows,transcriptQuery);query.addTextChangedListener(watcher(value->{transcriptQuery=value;populateTranscript(rows,value);}));
    }

    private void populateTranscript(LinearLayout rows,String query){
        rows.removeAllViews();JSONArray segments=result.optJSONArray("segments");int count=0;
        for(int i=0;i<segments.length();i++){
            JSONObject seg=segments.optJSONObject(i);if(seg==null)continue;
            if(!query.trim().isEmpty()&&!MeetingStore.normalizeSearch(seg.optString("text")+" "+speakerName(seg.optString("speaker_id"))).contains(MeetingStore.normalizeSearch(query.trim())))continue;
            count++;LinearLayout row=card();row.addView(label(speakerName(seg.optString("speaker_id"))+" · "+time(seg.optDouble("start"))+" – "+time(seg.optDouble("end")),13,TEAL,true));row.addView(label(seg.optString("text"),16,INK,false));
            File audio=resultAudio();if(audio!=null)row.addView(button("استماع من "+time(seg.optDouble("start")),false,()->playAt(audio,seg.optDouble("start"))));
            Button edit=button("تعديل النص والمتحدث",false,()->editSegment(seg));edit.setEnabled(!analysisRunning);row.addView(edit);rows.addView(row);
            String annotation=speechNote(result,seg.optString("id"));if(!annotation.isEmpty())row.addView(label(annotation,13,MUTED,false));
            String conditions=fixtureConditions(result,seg.optString("id"));if(!conditions.isEmpty())row.addView(label(conditions,13,MUTED,false));
            Button language=button("تحديد اللغة ووضوح الكلام",false,()->editSpeechFlag(seg));language.setEnabled(!analysisRunning);row.addView(language);
            String codeNote=codeNote(result,seg.optString("id"));if(!codeNote.isEmpty())row.addView(label(codeNote,13,MUTED,false));
            Button decode=button("فك ترميز النص أو توثيق عدم فهمه",false,()->inspectCode(seg));decode.setEnabled(!analysisRunning);row.addView(decode);
        }
        if(count==0)rows.addView(label(segments.length()==0?"لم يظهر كلام واضح في التسجيل.":"لا توجد مقاطع تطابق البحث.",15,MUTED,false));
    }

    private void editSegment(JSONObject segment){
        if(analysisRunning){toast("انتظر انتهاء التحليل قبل التعديل.");return;}
        LinearLayout fields=new LinearLayout(this);fields.setOrientation(LinearLayout.VERTICAL);fields.setPadding(dp(18),0,dp(18),0);
        EditText text=edit("نص المقطع",segment.optString("text"),false);text.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_MULTI_LINE);text.setSingleLine(false);text.setMinLines(3);fields.addView(text);
        JSONArray people=result.optJSONArray("speakers");String[] names=new String[people.length()];int selected=0;
        for(int i=0;i<people.length();i++){JSONObject person=people.optJSONObject(i);names[i]=person.optString("name");if(person.optString("id").equals(segment.optString("speaker_id")))selected=i;}
        Spinner speaker=new Spinner(this);speaker.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names));speaker.setSelection(selected);fields.addView(speaker);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("تصحيح المقطع").setView(fields).setNegativeButton("إلغاء",null).setPositiveButton("حفظ التعديل",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try{JSONObject changed=MeetingEdits.applySegmentEdit(result,segment.optString("id"),text.getText().toString(),people.getJSONObject(speaker.getSelectedItemPosition()).getString("id"));invalidateTranslations(changed);changed.remove("_evaluation");JSONObject flags=changed.optJSONObject("_speech_flags");if(flags!=null)flags.remove(segment.optString("id"));JSONObject codes=changed.optJSONObject("_code_notes");if(codes!=null)codes.remove(segment.optString("id"));commitEdit(changed,true);dialog.dismiss();}
            catch(Exception ex){text.setError(ex.getMessage());}
        }));dialog.show();
    }

    private void commitEdit(JSONObject changed,boolean render)throws Exception{
        if(analysisRunning)throw new Exception("انتظر انتهاء التحليل قبل حفظ التعديل.");
        if(changed.has("_id"))store.saveMeeting(changed);result=changed;if(render)showResult();
    }

    private void editSummary(){
        if(analysisRunning){toast("انتظر انتهاء التحليل قبل التعديل.");return;}
        EditText text=edit("الملخص",result.optJSONObject("minutes").optString("summary"),false);text.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_MULTI_LINE);text.setSingleLine(false);text.setMinLines(4);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("تعديل الملخص").setView(text).setNegativeButton("إلغاء",null).setPositiveButton("حفظ التعديل",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try{String summary=text.getText().toString().trim();if(summary.isEmpty()||summary.length()>12000)throw new Exception("أدخل ملخصًا بين حرف و12 ألف حرف.");JSONObject changed=new JSONObject(result.toString());changed.getJSONObject("minutes").put("summary",summary);changed.put("_summary_edited",true);invalidateTranslations(changed);commitEdit(changed,true);dialog.dismiss();}
            catch(Exception ex){text.setError(ex.getMessage());}
        }));dialog.show();
    }

    private void resummarize(){
        if(analysisRunning||importRunning||RecordingService.isRecording())return;
        final String url=prefs.getString("server_url",""),token=prefs.getString("access_token","");
        if(url.isEmpty()||token.isEmpty()){error("اضبط رابط الخدمة ورمز الوصول في الإعدادات أولًا.");return;}
        new AlertDialog.Builder(this).setTitle("إعادة تلخيص النص المصحح؟")
            .setMessage("سيُرسل النص وأسماء المتحدثين إلى "+url+" ثم خدمة OpenAI لإعداد محضر جديد. هذه الخطوة لا ترسل الصوت. سيحل المحضر الجديد محل الملخص والقرارات والمهام الحالية، وتُعاد حالة إنجاز المهام. قد تحتسب تكلفة معالجة.")
            .setNegativeButton("إلغاء",null).setPositiveButton("موافق، أرسل النص",(d,w)->{
                if(analysisRunning)return;
                try{final JSONObject input=new JSONObject(result.toString());analysisRunning=true;showResult();
                    worker.execute(()->{try{
                        JSONObject updated=MeetingApi.summarize(url,token,input);
                        for(String key:new String[]{"_id","_saved_at","_audio_path","_demo","_fixture","_baseline_segments","_analysis_seconds","_audio_bytes","_device","_capture_kind","_signal","_test_notes","_evaluation","_translations","_speech_flags","_code_notes"})if(input.has(key))updated.put(key,input.get(key));
                        invalidateTranslations(updated);
                        updated.put("_minutes_stale",false);updated.put("_summary_edited",false);store.saveMeeting(updated);
                        handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("saved",true));if(!isDestroyed()&&!isFinishing()){result=updated;resultTab="minutes";showResult();}});
                    }catch(Exception ex){handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("error",ex.getMessage()));if(!isDestroyed()&&!isFinishing()&&"result".equals(screen))showResult();});}});
                }catch(Exception ex){analysisRunning=false;error(ex.getMessage());}
            }).show();
    }

    private interface TextChange{void changed(String text);}
    private void setTextIfChanged(TextView view,String text){if(!view.getText().toString().equals(text))view.setText(text);}
    private TextWatcher watcher(TextChange change){return new TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){change.changed(s.toString());}public void afterTextChanged(Editable e){}};}

    private void chooseTranslation(){
        new AlertDialog.Builder(this).setTitle("لغة الترجمة").setItems(TRANSLATION_NAMES,(d,index)->{
            translationLanguage=TRANSLATION_CODES[index];resultTab="translation";showResult();
        }).show();
    }

    private void renderTranslation(){
        LinearLayout intro=card();intro.addView(label("ترجمة اللقاء · "+translationLanguage,22,INK,true));
        intro.addView(label("ترجمة آلية للنص والمحضر، مع إبقاء أسماء المتحدثين وأوقات المقاطع. راجع المصطلحات والمعنى قبل المشاركة.",14,MUTED,false));
        JSONObject all=result.optJSONObject("_translations"),translated=all==null?null:all.optJSONObject(translationLanguage);
        if(translated!=null&&translated.optBoolean("_stale",false))intro.addView(label("تغيّر المصدر بعد الترجمة؛ حدّثها قبل اعتمادها.",14,Color.rgb(161,94,12),true));
        Button translate=button(analysisRunning?"جارٍ المعالجة…":translated==null?"إنشاء الترجمة":"تحديث الترجمة",true,()->requestTranslation());
        translate.setEnabled(!analysisRunning&&!importRunning&&!RecordingService.isRecording());intro.addView(translate);
        intro.addView(button("اختيار لغة أخرى",false,()->chooseTranslation()));content.addView(intro);
        if(translated==null){section("النص المترجم","اختر إنشاء الترجمة، ثم راجع تأكيد إرسال النص إلى الخدمة.");return;}
        section("المحضر المترجم",translated.optString("translated_report"));
        JSONArray texts=translated.optJSONArray("segments"),source=result.optJSONArray("segments");
        if(texts!=null)for(int i=0;i<texts.length();i++){
            JSONObject line=texts.optJSONObject(i),original=source.optJSONObject(i);if(line==null||original==null)continue;
            LinearLayout row=card();row.addView(label(speakerName(original.optString("speaker_id"))+" · "+time(original.optDouble("start")),13,TEAL,true));row.addView(label(line.optString("text"),16,INK,false));content.addView(row);
        }
    }

    private void requestTranslation(){
        if(analysisRunning||importRunning||RecordingService.isRecording())return;
        final String url=prefs.getString("server_url",""),token=prefs.getString("access_token","");
        if(url.isEmpty()||token.isEmpty()){error("اضبط رابط الخدمة ورمز الوصول في الإعدادات أولًا.");return;}
        final String target=translationLanguage;
        new AlertDialog.Builder(this).setTitle("إرسال النص للترجمة؟")
            .setMessage("سيُرسل النص والمحضر وأسماء المتحدثين إلى "+url+" ثم خدمة OpenAI للترجمة إلى "+target+". لا يُرسل الصوت في هذه العملية. قد تُحتسب تكلفة، وتحتاج الترجمة مراجعة."+(result.optBoolean("_minutes_stale")?"\nالمحضر الحالي يحتاج إعادة تلخيص بعد تصحيح النص؛ الترجمة ستستخدم نسخته الحالية.":""))
            .setNegativeButton("إلغاء",null).setPositiveButton("موافق، ترجم النص",(d,w)->{
                if(analysisRunning)return;
                try{final JSONObject input=new JSONObject(result.toString());analysisRunning=true;showResult();
                    worker.execute(()->{try{
                        JSONObject translated=MeetingApi.translate(url,token,input,target);translated.put("_stale",false);
                        JSONObject next=new JSONObject(input.toString()),all=next.optJSONObject("_translations");if(all==null)all=new JSONObject();
                        all.put(target,translated);next.put("_translations",all);boolean stale=false;java.util.Iterator<String> keys=all.keys();while(keys.hasNext())if(all.getJSONObject(keys.next()).optBoolean("_stale"))stale=true;next.put("_translations_stale",stale);
                        store.saveMeeting(next);
                        handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("saved",true));if(!isDestroyed()&&!isFinishing()){result=next;translationLanguage=target;resultTab="translation";showResult();}});
                    }catch(Exception ex){handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("error",ex.getMessage()));if(!isDestroyed()&&!isFinishing()&&"result".equals(screen))showResult();});}});
                }catch(Exception ex){analysisRunning=false;error(ex.getMessage());}
            }).show();
    }

    private void invalidateTranslations(JSONObject meeting)throws Exception{
        meeting.put("_translations_stale",true);JSONObject all=meeting.optJSONObject("_translations");
        if(all!=null){java.util.Iterator<String> keys=all.keys();while(keys.hasNext())all.getJSONObject(keys.next()).put("_stale",true);}
    }

    private String languageName(String code){
        if("ar".equals(code))return "العربية";if("mul".equals(code))return "متعدد اللغات";if("other".equals(code))return "لغة أخرى";if("unknown".equals(code))return "لغة غير محددة";
        for(int i=0;i<TRANSLATION_CODES.length;i++)if(TRANSLATION_CODES[i].equals(code))return TRANSLATION_NAMES[i];return "لغة غير محددة";
    }

    private String speechStatusName(String status){
        if("clear".equals(status))return "مفهوم";if("unclear".equals(status))return "غير واضح";
        if("coded_by_user".equals(status))return "مرمز/مشفر بحسب توثيق المستخدم";
        return "غير قابل للفهم؛ التشفير غير متحقق";
    }

    private JSONObject speechFlag(JSONObject meeting,String id){
        JSONObject flags=meeting.optJSONObject("_speech_flags");if(flags!=null&&flags.optJSONObject(id)!=null)return flags.optJSONObject(id);
        JSONObject minutes=meeting.optJSONObject("minutes");JSONArray annotations=minutes==null?null:minutes.optJSONArray("speech_annotations");
        if(annotations!=null)for(int i=0;i<annotations.length();i++){JSONObject annotation=annotations.optJSONObject(i);if(annotation!=null&&id.equals(annotation.optString("segment_id")))return annotation;}
        return null;
    }

    private String speechNote(JSONObject meeting,String id){
        JSONObject flag=speechFlag(meeting,id);if(flag==null)return "";
        return languageName(flag.optString("language","unknown"))+" · "+speechStatusName(flag.optString("status","uninterpretable"))+"\n"+flag.optString("reason")+
            (flag.optBoolean("reviewed_by_user")?" · تسمية راجعها المستخدم":meeting.has("_fixture")?" · وصف محدد في المرجع المصطنع":meeting.optBoolean("_minutes_stale")?" · تقدير آلي قديم بعد تصحيح النص":" · تقدير آلي يحتاج مراجعة");
    }

    private void editSpeechFlag(JSONObject segment){
        if(analysisRunning)return;String id=segment.optString("id");JSONObject previous=speechFlag(result,id);
        String[] codes=new String[TRANSLATION_CODES.length+4],names=new String[codes.length];codes[0]="ar";names[0]="العربية";
        for(int i=0;i<TRANSLATION_CODES.length;i++){codes[i+1]=TRANSLATION_CODES[i];names[i+1]=TRANSLATION_NAMES[i];}
        codes[codes.length-3]="mul";names[names.length-3]="متعدد اللغات";codes[codes.length-2]="other";names[names.length-2]="لغة أخرى";codes[codes.length-1]="unknown";names[names.length-1]="لغة غير محددة";
        String[] statuses={"clear","unclear","uninterpretable","coded_by_user"};
        LinearLayout fields=new LinearLayout(this);fields.setOrientation(LinearLayout.VERTICAL);fields.setPadding(dp(18),0,dp(18),0);
        fields.addView(label("يرتبط التنبيه بهذا المتحدث ووقت المقطع. اختر لغة غير محددة إذا لم تتأكد. تسمية مشفر تحتاج توثيقك؛ التطبيق لا يؤكد التشفير أو يفكه تلقائيًا.",13,MUTED,false));
        Spinner language=new Spinner(this);language.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names));int selection=codes.length-1;
        for(int i=0;i<codes.length;i++)if(previous!=null&&codes[i].equals(previous.optString("language")))selection=i;language.setSelection(selection);fields.addView(language);
        Spinner clarity=new Spinner(this);clarity.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,new String[]{"مفهوم","غير واضح","غير قابل للفهم","مرمز/مشفر بتوثيقي"}));
        for(int i=0;i<statuses.length;i++)if(previous!=null&&statuses[i].equals(previous.optString("status")))clarity.setSelection(i);fields.addView(clarity);
        EditText reason=edit("سبب التسمية أو مرجع التوثيق",previous==null?"":previous.optString("reason"),false);reason.setSingleLine(false);fields.addView(reason);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("لغة المقطع ووضوحه").setView(fields).setNegativeButton("إلغاء",null).setPositiveButton("حفظ التسمية",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            String note=reason.getText().toString().trim();if(note.isEmpty()||note.length()>300){reason.setError("اكتب سببًا أو توثيقًا حتى 300 حرف.");return;}
            try{JSONObject next=new JSONObject(result.toString()),flags=next.optJSONObject("_speech_flags");if(flags==null)flags=new JSONObject();
                JSONObject flag=new JSONObject();flag.put("segment_id",id);flag.put("language",codes[language.getSelectedItemPosition()]);flag.put("status",statuses[clarity.getSelectedItemPosition()]);flag.put("reason",note);flag.put("reviewed_by_user",true);
                flags.put(id,flag);next.put("_speech_flags",flags);commitEdit(next,true);dialog.dismiss();
            }catch(Exception ex){error(ex.getMessage());}
        }));dialog.show();
    }

    private void configureTest(){
        EditText notes=edit("المسافة، الضجيج، المشاركون والميكروفون",prefs.getString("test_notes",""),false);notes.setSingleLine(false);notes.setMinLines(3);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("تجربة قياس موثقة").setMessage("جرّب الاجتماع نفسه في ظروف واضحة: مثل متر واحد أو ثلاثة أمتار، غرفة هادئة أو ضجيج. أدخل الظروف الفعلية وأعلم المشاركين. لا يستنتج التطبيق المسافة أو اسم شخص مجهول من الصوت.")
            .setView(notes).setNegativeButton("رجوع",null).setPositiveButton("تفعيل الاختبار",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String value=notes.getText().toString().trim();if(value.isEmpty()||value.length()>1000){notes.setError("اكتب وصفًا للظروف من حرف إلى ألف حرف.");return;}prefs.edit().putBoolean("test_mode",true).putString("test_notes",value).apply();dialog.dismiss();showMeeting();}));dialog.show();
    }

    private void evaluateMeeting(){
        if(analysisRunning||importRunning)return;
        if(result.optBoolean("_demo")||!result.has("_baseline_segments")){error("يتطلب القياس تسجيلًا حقيقيًا حُلّل بهذه النسخة.");return;}
        LinearLayout fields=new LinearLayout(this);fields.setOrientation(LinearLayout.VERTICAL);fields.setPadding(dp(18),0,dp(18),0);
        fields.addView(label("استمع للتسجيل من تبويب النص، وصحح جميع الكلمات ونسبة كل مقطع إلى المتحدث. الحساب يقارن التفريغ الأصلي بمرجعك المصحح. لا يقيس الترجمة أو جودة القرارات، ولا يمثل تقييمًا مستقلاً دون صحة هذا المرجع.",14,MUTED,false));
        EditText notes=edit("ظروف التجربة",result.optString("_test_notes",""),false);notes.setSingleLine(false);fields.addView(notes);
        CheckBox verified=new CheckBox(this);verified.setText("استمعت وصححت جميع المقاطع والمتحدثين كمرجع للاختبار");fields.addView(verified);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("قياس الدقة بعد المراجعة").setView(fields).setNegativeButton("إلغاء",null).setPositiveButton("احسب محليًا",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            if(!verified.isChecked()){toast("أكمل الاستماع والمراجعة أولًا.");return;}String conditions=notes.getText().toString().trim();if(conditions.isEmpty()||conditions.length()>1000){notes.setError("صف ظروف التجربة حتى ألف حرف.");return;}
            try{final JSONObject input=new JSONObject(result.toString());input.put("_test_notes",conditions);analysisRunning=true;dialog.dismiss();showResult();
                worker.execute(()->{try{
                    JSONObject score=MeetingEvaluation.compare(input.getJSONArray("_baseline_segments"),input.getJSONArray("segments"));
                    score.put("human_reviewed",true);score.put("conditions",conditions);score.put("device",input.optString("_device"));score.put("capture",input.optString("_capture_kind"));score.put("analysis_seconds",input.optDouble("_analysis_seconds"));score.put("audio_bytes",input.optLong("_audio_bytes"));score.put("measured_at",System.currentTimeMillis());
                    if(input.has("_signal"))score.put("signal",input.getJSONObject("_signal"));
                    input.put("_evaluation",score);store.saveMeeting(input);
                    handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()));if(!isDestroyed()&&!isFinishing()){result=input;showResult();toast("تم حفظ القياس المحلي مع مرجعك المصحح.");}});
                }catch(Exception ex){handler.post(()->{analysisRunning=false;busy=importRunning;getApplicationContext().sendBroadcast(new Intent(ANALYSIS_UPDATE).setPackage(getPackageName()).putExtra("error",ex.getMessage()));if(!isDestroyed()&&!isFinishing()&&"result".equals(screen))showResult();});}});
            }catch(Exception ex){analysisRunning=false;error(ex.getMessage());}
        }));dialog.show();
    }

    private String evaluationText(JSONObject score){
        JSONObject signal=score.optJSONObject("signal");
        return "الهاتف: "+score.optString("device")+"\nمصدر الصوت: "+score.optString("capture")+"\nالظروف: "+score.optString("conditions")+
            (signal==null?"":"\nذروة إشارة الميكروفون: "+String.format(Locale.US,"%.1f",signal.optDouble("peak_amplitude_percent"))+"% من مجال الجهاز؛ ليست قياس ديسيبل أو دليلًا على وضوح الكلام")+
            "\nزمن التحليل: "+String.format(Locale.US,"%.1f",score.optDouble("analysis_seconds"))+" ثانية"+
            "\nأخطاء الكلمات: "+score.optInt("word_errors")+" من "+score.optInt("reference_words")+" كلمة مرجعية"+
            "\nمعدل خطأ الكلمات WER: "+String.format(Locale.US,"%.1f",score.optDouble("word_error_rate_percent"))+"% (الأقل أفضل؛ يمكن أن يتجاوز 100% بسبب الكلمات المضافة)"+
            (score.has("character_error_rate_percent")&&!score.isNull("character_error_rate_percent")?"\nمعدل خطأ الأحرف CER: "+String.format(Locale.US,"%.1f",score.optDouble("character_error_rate_percent"))+"%":"\nCER غير محسوب؛ الحد 6000 حرف مطبّع في كل نسخة.")+
            (score.optBoolean("has_cjk")?"\nللنص الصيني استخدم CER؛ WER القائم على فصل الكلمات غير مناسب للنص دون مسافات.":"")+
            "\nنسبة زمن المقاطع التي صححت إسنادها إلى المتحدث: "+String.format(Locale.US,"%.1f",score.optDouble("speaker_correction_percent"))+"%"+
            "\nعدد المقاطع المعدّلة: "+score.optInt("changed_segments")+
            "\nمرجع الحساب هو النص والإسناد اللذان راجعتهما أنت. قياس إسناد المتحدثين يعتمد حدود المقاطع الحالية، وليس DER معياريًا، ولا يثبت التقاط الكلام الذي غاب كليًا عن التسجيل أو دقة المحضر والترجمة.";
    }

    private void renderEvaluation(JSONObject score){section("نتيجة تجربة موثقة",evaluationText(score));}

    private String exportText(JSONObject meeting){
        StringBuilder text=new StringBuilder(store.report(meeting));JSONObject score=meeting.optJSONObject("_evaluation");if(score!=null)text.append("\nنتيجة تجربة موثقة\n").append(evaluationText(score)).append("\n");
        JSONObject fixture=meeting.optJSONObject("_fixture");if(fixture!=null)text.append("\nمرجع مصطنع للتجربة\n").append(fixture.optString("notice_ar")).append("\n");
        JSONArray segments=meeting.optJSONArray("segments");
        if(segments!=null)for(int i=0;i<segments.length();i++){JSONObject segment=segments.optJSONObject(i);if(segment==null)continue;JSONObject flag=speechFlag(meeting,segment.optString("id"));if(flag!=null&&(!"ar".equals(flag.optString("language"))||!"clear".equals(flag.optString("status"))))text.append("\nلغة ووضوح المقطع ").append(segment.optString("id")).append(" · ").append(speakerName(meeting,segment.optString("speaker_id"))).append(" · ").append(time(segment.optDouble("start"))).append("\n").append(speechNote(meeting,segment.optString("id"))).append("\n");}
        if(segments!=null)for(int i=0;i<segments.length();i++){JSONObject segment=segments.optJSONObject(i);if(segment==null)continue;String note=codeNote(meeting,segment.optString("id"));if(!note.isEmpty())text.append("\nترميز المقطع ").append(segment.optString("id")).append(" · ").append(speakerName(meeting,segment.optString("speaker_id"))).append(" · ").append(time(segment.optDouble("start"))).append("\n").append(note).append("\n");}
        JSONObject all=meeting.optJSONObject("_translations");if(all!=null){java.util.Iterator<String> keys=all.keys();while(keys.hasNext()){String code=keys.next();JSONObject translated=all.optJSONObject(code);if(translated!=null)text.append("\nالترجمة: ").append(code).append(translated.optBoolean("_stale")?" · المصدر تغيّر بعد الترجمة":"").append("\n").append(translated.optString("translated_report")).append("\n");}}
        return text.toString();
    }
    private String speakerName(String id){return speakerName(result,id);}
    private String speakerName(JSONObject meeting,String id){JSONArray speakers=meeting.optJSONArray("speakers");for(int i=0;i<speakers.length();i++){JSONObject s=speakers.optJSONObject(i);if(id.equals(s.optString("id")))return s.optString("name");}return "متحدث";}
    private void renameSpeaker(JSONObject speaker){
        if(analysisRunning){toast("انتظر انتهاء التحليل قبل التعديل.");return;}
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
                    invalidateTranslations(updated);
                    commitEdit(updated,true);
                }catch(Exception ex){error("تعذر حفظ الاسم.");}
            }).show();
    }

    private JSONObject readFixtureAsset(String asset)throws Exception{
        if(!asset.matches("fixtures/[a-z0-9-]+\\.json"))throw new Exception("مسار تجربة غير صالح.");
        try(InputStream input=getAssets().open(asset);java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream()){
            byte[] buffer=new byte[4096];int n;while((n=input.read(buffer))!=-1){if(bytes.size()+n>300_000)throw new Exception("بيانات التجربة أكبر من الحد.");bytes.write(buffer,0,n);}
            return new JSONObject(new String(bytes.toByteArray(),StandardCharsets.UTF_8));
        }
    }

    private void showFixtures(){
        screen="fixtures";shell("مختبر قابل لإعادة التجربة");
        try{
            JSONObject index=readFixtureAsset("fixtures/index.json");section("تجارب مصطنعة",index.optString("notice_ar"));
            JSONArray cases=index.getJSONArray("fixtures");
            for(int i=0;i<cases.length();i++){
                JSONObject item=cases.getJSONObject(i);LinearLayout row=card();row.addView(label(item.getString("title"),21,INK,true));row.addView(label(item.optString("description_ar"),14,MUTED,false));
                row.addView(button("فتح مرجع التجربة",true,()->{try{result=readFixtureAsset(item.getString("asset"));resultTab="transcript";transcriptQuery="";showResult();}catch(Exception ex){error(ex.getMessage());}}));content.addView(row);
            }
            section("اختبار صوت فعلي", "تتوفر ملفات صوت مصطنعة ومرجعها في فرع التطبيق على GitHub، ضمن downloads/fixtures. استورد الملف ثم وافق على التحليل، وقارن الناتج بمرجعه. قائمة هذه الشاشة تتبع المرجع المعروف؛ لا تقيس التعرف الآلي. لا يتطلب استعراض المرجع اتصالًا.");
            content.addView(button("تنزيل الصوت التجريبي ومرجعه",false,()->{try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://raw.githubusercontent.com/alhasanelabed-sys/chats/refs/heads/app/majlis-android/downloads/fixtures/majlis-fixtures.zip")));}catch(Exception ex){error("تعذر فتح المتصفح؛ افتح downloads/fixtures في مستودع التطبيق.");}}));
        }catch(Exception ex){error("تعذر فتح بيانات التجارب: "+ex.getMessage());}
    }

    private void simulateRoster(){
        if(result==null||!result.has("_fixture"))return;
        final JSONObject meeting=result;final JSONArray segments=meeting.optJSONArray("segments");if(segments==null||segments.length()==0)return;
        LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(dp(18),0,dp(18),dp(12));
        body.addView(label("محاكاة من مرجع مصطنع. تُضاف الهوية عند أول مقطع لها، وتبقى الهوية نفسها عند العودة. كل خطوة مقطع؛ التشغيل مسرّع ولا يستمع إلى الميكروفون.",13,MUTED,false));
        TextView event=label("",15,TEAL,true),roster=label("",14,INK,false),utterance=label("",15,INK,false);body.addView(event);body.addView(roster);body.addView(utterance);
        final int[] count={0};final boolean[] running={false};
        Runnable refresh=()->{
            java.util.LinkedHashMap<String,Integer> turns=new java.util.LinkedHashMap<>();java.util.LinkedHashMap<String,Double> first=new java.util.LinkedHashMap<>();
            for(int j=0;j<count[0];j++){JSONObject seg=segments.optJSONObject(j);String id=seg.optString("speaker_id");if(!turns.containsKey(id)){turns.put(id,0);first.put(id,seg.optDouble("start"));}turns.put(id,turns.get(id)+1);}
            StringBuilder list=new StringBuilder("القائمة: "+turns.size()+" / "+meeting.optJSONArray("speakers").length()+" متحدثين\n");
            for(String id:turns.keySet())list.append(speakerName(meeting,id)).append(" · أول كلام ").append(time(first.get(id))).append(" · مرات الكلام ").append(turns.get(id)).append("\n");roster.setText(list.toString());
            if(count[0]==0){event.setText("لم يبدأ أحد الكلام بعد");utterance.setText("اضغط المقطع التالي أو تشغيل المحاكاة.");return;}
            JSONObject seg=segments.optJSONObject(count[0]-1);String id=seg.optString("speaker_id");event.setText((turns.get(id)==1?"إضافة متحدث إلى القائمة: ":"عودة متحدث مسجل: ")+speakerName(meeting,id)+" · "+time(seg.optDouble("start"))+" · "+count[0]+"/"+segments.length());utterance.setText(seg.optString("text")+"\n"+speechNote(meeting,seg.optString("id"))+"\n"+fixtureConditions(meeting,seg.optString("id")));
        };
        final Button play=button("تشغيل المحاكاة",true,()->{});body.addView(play);
        final Runnable[] tick=new Runnable[1];tick[0]=()->{if(!running[0]||fixtureDialog==null||!fixtureDialog.isShowing())return;if(count[0]<segments.length()){count[0]++;refresh.run();}if(count[0]<segments.length())handler.postDelayed(tick[0],1800);else{running[0]=false;play.setText("إعادة التشغيل");}};
        play.setOnClickListener(v->{if(running[0]){running[0]=false;handler.removeCallbacks(tick[0]);play.setText("متابعة المحاكاة");}else{if(count[0]>=segments.length())count[0]=0;running[0]=true;play.setText("إيقاف المحاكاة مؤقتًا");tick[0].run();}});
        body.addView(button("المقطع التالي",false,()->{running[0]=false;handler.removeCallbacks(tick[0]);if(count[0]<segments.length())count[0]++;refresh.run();play.setText("متابعة المحاكاة");}));
        body.addView(button("إعادة ضبط القائمة",false,()->{running[0]=false;handler.removeCallbacks(tick[0]);count[0]=0;refresh.run();play.setText("تشغيل المحاكاة");}));
        ScrollView scroll=new ScrollView(this);scroll.addView(body);fixtureDialog=new AlertDialog.Builder(this).setTitle("محاكاة قائمة المتحدثين").setView(scroll).setPositiveButton("إغلاق",null).create();fixtureDialog.setOnDismissListener(d->{running[0]=false;handler.removeCallbacks(tick[0]);fixtureDialog=null;});refresh.run();fixtureDialog.show();
    }

    private String fixtureConditions(JSONObject meeting,String id){
        JSONObject fixture=meeting.optJSONObject("_fixture");JSONArray events=fixture==null?null:fixture.optJSONArray("events");if(events==null)return "";
        for(int i=0;i<events.length();i++){
            JSONObject event=events.optJSONObject(i);if(event==null||!id.equals(event.optString("segment_id")))continue;
            String kind=event.optString("condition"),noise="white".equals(kind)?"ضوضاء بيضاء":"pink".equals(kind)?"ضوضاء وردية":"room_chatter".equals(kind)?"أصوات خلفية مصطنعة":"impulse".equals(kind)?"ضوضاء نبضية":"مرجع نصي أو صوت نظيف";
            return "ظروف مصطنعة: "+noise+(event.has("speech_gain_db")&&!event.isNull("speech_gain_db")?" · مستوى الإشارة الرقمي "+String.format(Locale.US,"%.0f",event.optDouble("speech_gain_db"))+" dB؛ لا يمثل مسافة مقاسة":"");
        }return "";
    }

    private String codeNote(JSONObject meeting,String id){
        JSONObject notes=meeting.optJSONObject("_code_notes"),note=notes==null?null:notes.optJSONObject(id);if(note==null)return "";
        return ("decoded".equals(note.optString("status"))?"فك ترميز محلي باختيار المستخدم: "+note.optString("scheme")+"\nالناتج: "+note.optString("decoded_text"):"لم يمكن فك النص؛ الأصل محفوظ ولا يوجد إثبات آلي للتشفير")+"\nالأصل: "+note.optString("encoded_text")+"\nالتوثيق: "+note.optString("reason");
    }

    private void inspectCode(JSONObject segment){
        if(analysisRunning)return;
        String[] schemes={"base64","hex","morse","rot13","caesar","unknown"};String[] names={"Base64 — ترميز","Hex — ترميز سداسي","مورس — حروف لاتينية وأرقام","ROT13 — إحلال بسيط","قيصر — بإزاحة معلومة","غير معروف أو مفتاحه غير متوفر"};
        LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(dp(18),0,dp(18),dp(12));body.addView(label("اختر طريقة معروفة للنص المكتوب أو الجزء المنسوخ منه. التنفيذ محلي ويحفظ الأصل مع المتحدث والوقت. لا يفك تشفيرًا حديثًا بلا مفتاح ولا يثبت أن كلامًا غير مفهوم مشفر.",13,MUTED,false));
        Spinner scheme=new Spinner(this);scheme.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names));body.addView(scheme);
        EditText source=edit("النص المرمز",segment.optString("text"),false);source.setSingleLine(false);source.setMinLines(2);body.addView(source);
        EditText shift=edit("إزاحة قيصر (0–25)","3",false);shift.setInputType(InputType.TYPE_CLASS_NUMBER);body.addView(shift);
        EditText reason=edit("سبب اختيار الطريقة أو عدم إمكان الفك","تحديد يدوي؛ راجع النص والمرجع.",false);reason.setSingleLine(false);body.addView(reason);
        ScrollView scroll=new ScrollView(this);scroll.addView(body);AlertDialog dialog=new AlertDialog.Builder(this).setTitle("فك وتوثيق النص").setView(scroll).setNegativeButton("إلغاء",null).setPositiveButton("فك وتوثيق",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            String encoded=source.getText().toString(),why=reason.getText().toString().trim(),selected=schemes[scheme.getSelectedItemPosition()];
            if(encoded.trim().isEmpty()||encoded.length()>20000){source.setError("أدخل نصًا من 1 إلى 20000 حرف.");return;}if(why.isEmpty()||why.length()>300){reason.setError("اكتب توثيقًا حتى 300 حرف.");return;}
            try{
                int amount=0;if("caesar".equals(selected)){try{amount=Integer.parseInt(shift.getText().toString());}catch(Exception ex){shift.setError("أدخل إزاحة من 0 إلى 25.");return;}if(amount<0||amount>25){shift.setError("أدخل إزاحة من 0 إلى 25.");return;}}
                String decoded="unknown".equals(selected)?null:TextCodecs.decode(selected,encoded,amount);
                JSONObject next=new JSONObject(result.toString()),notes=next.optJSONObject("_code_notes");if(notes==null)notes=new JSONObject();JSONObject note=new JSONObject();
                note.put("segment_id",segment.getString("id"));note.put("speaker_id",segment.getString("speaker_id"));note.put("start",segment.getDouble("start"));note.put("end",segment.getDouble("end"));note.put("segment_text",segment.getString("text"));note.put("encoded_text",encoded);note.put("scheme",selected);note.put("shift",amount);note.put("status",decoded==null?"unresolved":"decoded");if(decoded!=null)note.put("decoded_text",decoded);note.put("reason",why);note.put("reviewed_by_user",true);
                notes.put(segment.getString("id"),note);next.put("_code_notes",notes);commitEdit(next,true);dialog.dismiss();
            }catch(IllegalArgumentException ex){source.setError(ex.getMessage());}catch(Exception ex){error(ex.getMessage());}
        }));dialog.show();
    }

    private void showDemo() {
        try {
            result=new JSONObject("{\"title\":\"مثال تجريبي: إطلاق المنتج\",\"language\":\"ar\",\"duration_seconds\":92,\"_demo\":true,\"speakers\":[{\"id\":\"A\",\"name\":\"أحمد\",\"matched_reference\":false},{\"id\":\"B\",\"name\":\"سارة\",\"matched_reference\":false},{\"id\":\"C\",\"name\":\"خالد\",\"matched_reference\":false}],\"segments\":[{\"id\":\"s1\",\"speaker_id\":\"A\",\"start\":0,\"end\":14,\"text\":\"نراجع اليوم جاهزية إطلاق المنتج. نحتاج إلى اختبار النسخة قبل موعد الإطلاق.\"},{\"id\":\"s2\",\"speaker_id\":\"B\",\"start\":15,\"end\":32,\"text\":\"سأتولى اختبار النسخة وإرسال تقرير الأخطاء يوم الخميس.\"},{\"id\":\"s3\",\"speaker_id\":\"A\",\"start\":33,\"end\":48,\"text\":\"متفقون على إطلاق نسخة تجريبية محدودة بعد مراجعة تقرير الاختبار.\"},{\"id\":\"s4\",\"speaker_id\":\"C\",\"start\":60,\"end\":76,\"text\":\"انضممت الآن. سأجهز صفحة التعريف بالمنتج، ولم أحدد موعد تسليمها بعد.\"},{\"id\":\"s5\",\"speaker_id\":\"B\",\"start\":77,\"end\":92,\"text\":\"يبقى تحديد عدد المشاركين في التجربة. كم مستخدمًا نستهدف؟\"}],\"minutes\":{\"summary\":\"ناقش الفريق جاهزية المنتج، واتفق على إطلاق تجربة محدودة بعد مراجعة نتائج الاختبار. تولت سارة الاختبار، وتولى خالد صفحة التعريف. بقي حجم التجربة دون تحديد.\",\"discussion_points\":[\"جاهزية المنتج واختبار النسخة\",\"إطلاق تجربة محدودة\",\"إعداد صفحة التعريف وحجم التجربة\"],\"decisions\":[{\"text\":\"إطلاق نسخة تجريبية محدودة بعد مراجعة تقرير الاختبار.\",\"segment_ids\":[\"s3\"]}],\"action_items\":[{\"task\":\"اختبار النسخة وإرسال تقرير الأخطاء\",\"owner\":\"سارة\",\"due_date\":\"الخميس، كما ورد في الحديث\",\"segment_ids\":[\"s2\"]},{\"task\":\"تجهيز صفحة التعريف بالمنتج\",\"owner\":\"خالد\",\"due_date\":null,\"segment_ids\":[\"s4\"]}],\"open_questions\":[\"كم مستخدمًا سيشارك في النسخة التجريبية؟\"]}}");
            resultTab="minutes";showResult();
        } catch(Exception ex){error("تعذر فتح المثال التجريبي.");}
    }

    private File resultAudio(){
        if(result==null||result.optBoolean("_demo"))return null;
        File audio=new File(result.optString("_audio_path",""));
        try{if(!audio.getCanonicalFile().getParentFile().equals(store.recordingsDir().getCanonicalFile()))return null;}catch(Exception ex){return null;}
        return audio.isFile()&&audio.length()>0?audio:null;
    }

    private void addPlayback(LinearLayout parent,File file){
        playbackTime=label("استمع للتسجيل وراجع المقاطع بحسب وقتها",13,MUTED,false);parent.addView(playbackTime);
        playbackSeek=new SeekBar(this);playbackSeek.setMax(1000);playbackSeek.setLayoutDirection(View.LAYOUT_DIRECTION_LTR);parent.addView(playbackSeek);
        playbackButton=button("تشغيل التسجيل",false,()->{});
        playbackButton.setOnClickListener(v->{try{if(player==null)playAt(file,0);else if(player.isPlaying()){player.pause();playbackButton.setText("متابعة الاستماع");}else{player.start();playbackButton.setText("إيقاف الاستماع مؤقتًا");}}catch(Exception ex){stopPlayer();error("تعذر متابعة التسجيل.");}});
        parent.addView(playbackButton);
        playbackSeek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){
            public void onStartTrackingTouch(SeekBar seek){}
            public void onStopTrackingTouch(SeekBar seek){try{if(player!=null)player.seekTo((int)((long)seek.getProgress()*player.getDuration()/1000));}catch(Exception ignored){}}
            public void onProgressChanged(SeekBar seek,int progress,boolean fromUser){}
        });
    }

    private void play(File file){playAt(file,0);}
    private void playAt(File file,double seconds){
        if(RecordingService.isRecording()||sampleRecorder!=null){toast("أوقف التسجيل قبل الاستماع.");return;}
        stopPlayer();if(!file.isFile()){toast("ملف التسجيل غير موجود.");return;}
        try{
            final MediaPlayer fresh=new MediaPlayer();player=fresh;fresh.setDataSource(file.getAbsolutePath());
            fresh.setOnPreparedListener(p->{if(player!=p)return;p.seekTo((int)Math.min(Math.max(0,seconds*1000),p.getDuration()));p.start();if(playbackButton!=null)playbackButton.setText("إيقاف الاستماع مؤقتًا");startPlaybackTick();});
            fresh.setOnCompletionListener(p->{if(player==p)stopPlayer();});
            fresh.setOnErrorListener((p,what,extra)->{if(player==p){stopPlayer();error("تعذر تشغيل التسجيل.");}return true;});
            fresh.prepareAsync();
        }catch(Exception ex){stopPlayer();error("تعذر تشغيل التسجيل.");}
    }

    private void startPlaybackTick(){
        playbackTick=new Runnable(){public void run(){
            if(player==null)return;
            try{int position=player.getCurrentPosition(),duration=player.getDuration();if(playbackTime!=null)playbackTime.setText(time(position/1000.0)+" / "+time(duration/1000.0));if(playbackSeek!=null&&!playbackSeek.isPressed())playbackSeek.setProgress(duration==0?0:(int)((long)position*1000/duration));}catch(Exception ignored){}
            handler.postDelayed(this,300);
        }};handler.post(playbackTick);
    }

    private void stopPlayer(){
        if(playbackTick!=null){handler.removeCallbacks(playbackTick);playbackTick=null;}
        if(player!=null){try{player.release();}catch(Exception ignored){}player=null;}
        if(playbackButton!=null)playbackButton.setText("تشغيل التسجيل");
    }
    private void section(String title,String text){LinearLayout card=card();card.addView(label(title,20,INK,true));card.addView(label(text,16,INK,false));content.addView(card);}
    private void listSection(String title,JSONArray items,String empty){LinearLayout card=card();card.addView(label(title,20,INK,true));if(items==null||items.length()==0)card.addView(label(empty,14,MUTED,false));else for(int i=0;i<items.length();i++)card.addView(label("• "+items.optString(i),15,INK,false));content.addView(card);}
    private LinearLayout card(){LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);l.setPadding(dp(18),dp(18),dp(18),dp(18));GradientDrawable bg=new GradientDrawable();bg.setColor(Color.WHITE);bg.setCornerRadius(dp(18));l.setBackground(bg);LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,0,0,dp(14));l.setLayoutParams(p);return l;}
    private TextView label(String text,int size,int color,boolean bold){TextView v=new TextView(this);v.setText(text);v.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);v.setTextSize(size);v.setTextColor(color);v.setLineSpacing(dp(4),1);v.setPadding(0,dp(5),0,dp(5));if(bold)v.setTypeface(Typeface.DEFAULT,Typeface.BOLD);return v;}
    private EditText edit(String hint,String value,boolean password){EditText e=new EditText(this);e.setHint(hint);e.setText(value);e.setTextSize(15);e.setTextColor(INK);e.setSingleLine(true);e.setPadding(dp(10),dp(12),dp(10),dp(12));e.setInputType(InputType.TYPE_CLASS_TEXT|(password?InputType.TYPE_TEXT_VARIATION_PASSWORD:0));return e;}
    private Button button(String text,boolean primary,Runnable action){Button b=new Button(this);b.setText(text);b.setAllCaps(false);b.setTextSize(14);b.setTextColor(primary?Color.WHITE:TEAL);GradientDrawable bg=new GradientDrawable();bg.setColor(primary?TEAL:Color.rgb(235,245,242));bg.setCornerRadius(dp(12));b.setBackground(bg);b.setPadding(dp(12),dp(6),dp(12),dp(6));LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(48));p.setMargins(0,dp(10),0,0);b.setLayoutParams(p);b.setOnClickListener(v->{if("meeting".equals(screen)&&titleField!=null)prefs.edit().putString("last_title",titleField.getText().toString()).apply();stopPlayer();action.run();});return b;}
    private int dp(int n){return (int)(getResources().getDisplayMetrics().density*n+0.5f);}
    private String time(double seconds){long s=Math.max(0,(long)seconds);return String.format(Locale.US,"%02d:%02d",s/60,s%60);}
    private String date(long millis){return new SimpleDateFormat("yyyy/MM/dd HH:mm",Locale.US).format(new Date(millis));}
    private void toast(String message){Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    private void error(String message){if(isFinishing()||isDestroyed())return;new AlertDialog.Builder(this).setTitle("لم تكتمل العملية").setMessage(message==null||message.isEmpty()?"حدث خطأ. حاول مجددًا.":message).setPositiveButton("حسنًا",null).show();}
    @Override public void onBackPressed(){if("result".equals(screen)){showHistory();}else if(!"meeting".equals(screen)){showMeeting();}else super.onBackPressed();}
}
