package com.majlis.tests;

import android.app.Instrumentation;
import android.content.Context;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMetadataRetriever;
import android.os.Bundle;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.security.MessageDigest;

/** Invokes the distributed app's production codecs; never contacts an AI provider. */
public final class CloudAudioSmoke extends Instrumentation {
    private static final int SECONDS=80, RATE=48000;
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        Bundle evidence=new Bundle();
        File original=null,compressed=null,broken=null;
        try {
            Context context=getTargetContext();
            Class<?> type=Class.forName("com.majlis.app.CloudAudio",true,context.getClassLoader());
            Method prepare=type.getMethod("prepare",Context.class,File.class,double.class);
            original=new File(context.getCacheDir(),"cloud-smoke-original.wav");
            try(OutputStream out=new BufferedOutputStream(new java.io.FileOutputStream(original))){
                int bytes=SECONDS*RATE*4;
                out.write(new byte[]{'R','I','F','F'});le(out,36+bytes,4);out.write(new byte[]{'W','A','V','E','f','m','t',' '});
                le(out,16,4);le(out,1,2);le(out,2,2);le(out,RATE,4);le(out,RATE*4,4);le(out,4,2);le(out,16,2);
                out.write(new byte[]{'d','a','t','a'});le(out,bytes,4);
                for(int frame=0;frame<SECONDS*RATE;frame++){
                    le(out,(int)(9000*Math.sin(2*Math.PI*300*frame/RATE)),2);
                    le(out,(int)(7000*Math.sin(2*Math.PI*700*frame/RATE)),2);
                }
            }
            long originalBytes=original.length();byte[] hash=digest(original);
            compressed=(File)prepare.invoke(null,context,original,(double)SECONDS);
            check(!compressed.equals(original),"large input was not compressed");
            check(compressed.isFile()&&compressed.length()>500&&compressed.length()<=13000000,"invalid compressed size");
            check(original.length()==originalBytes&&MessageDigest.isEqual(hash,digest(original)),"source changed during compression");
            double duration;
            MediaMetadataRetriever reader=new MediaMetadataRetriever();
            try {reader.setDataSource(compressed.getAbsolutePath());duration=Long.parseLong(reader.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION))/1000.0;}
            finally {reader.release();}
            check(Math.abs(duration-SECONDS)<1.0,"compressed duration changed");
            MediaExtractor extractor=new MediaExtractor();
            try {
                extractor.setDataSource(compressed.getAbsolutePath());check(extractor.getTrackCount()==1,"unexpected track count");
                MediaFormat format=extractor.getTrackFormat(0);
                check("audio/mp4a-latm".equals(format.getString(MediaFormat.KEY_MIME)),"not AAC");
                check(format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)==1,"not mono");
                check(format.getInteger(MediaFormat.KEY_SAMPLE_RATE)==16000,"not 16kHz");
            } finally {extractor.release();}
            Class<?> pcm=Class.forName("com.majlis.app.AudioPcm",true,context.getClassLoader());
            Method decode=pcm.getDeclaredMethod("decode",File.class,File.class);decode.setAccessible(true);
            Object decoded=decode.invoke(null,compressed,new File(context.getCacheDir(),"majlis-cloud-audio"));
            double rms;
            try{
                java.lang.reflect.Field field=decoded.getClass().getDeclaredField("file");field.setAccessible(true);
                byte[] samples=new byte[32000];
                try(java.io.RandomAccessFile input=new java.io.RandomAccessFile((File)field.get(decoded),"r")){input.seek(32000);input.readFully(samples);}
                double energy=0;
                for(int n=0;n<samples.length;n+=2){double value=(short)((samples[n]&255)|((samples[n+1]&255)<<8))/32768.0;energy+=value*value;}
                rms=Math.sqrt(energy/(samples.length/2));check(rms>0.02,"encoded test audio became silent");
            }finally{((AutoCloseable)decoded).close();}
            byte[] compressedHash=digest(compressed);
            File fast=(File)prepare.invoke(null,context,compressed,duration);
            check(fast.equals(compressed)&&MessageDigest.isEqual(compressedHash,digest(fast)),"small source was unnecessarily changed");
            boolean refused=false;
            try{prepare.invoke(null,context,original,Double.NaN);}catch(InvocationTargetException expected){refused=true;}
            check(refused,"invalid duration accepted");
            broken=new File(context.getCacheDir(),"cloud-smoke-broken.wav");
            try(java.io.RandomAccessFile out=new java.io.RandomAccessFile(broken,"rw")){out.setLength(14000000);}
            refused=false;
            try{prepare.invoke(null,context,broken,(double)SECONDS);}catch(InvocationTargetException expected){refused=true;}
            check(refused&&broken.length()==14000000,"corrupt input accepted or source deleted");
            File[] leftovers=new File(context.getCacheDir(),"majlis-cloud-audio").listFiles();
            if(leftovers!=null)for(File file:leftovers){
                check(!file.getName().endsWith(".pcm"),"temporary PCM was retained");
                check(file.equals(compressed),"failed conversion retained a temporary file");
            }
            evidence.putString("result","PASS");evidence.putLong("source_bytes",originalBytes);
            evidence.putLong("compressed_bytes",compressed.length());evidence.putDouble("compressed_seconds",duration);
            evidence.putDouble("decoded_rms",rms);
            evidence.putString("source_sha256_unchanged","true");
            evidence.putString("codec","AAC mono 16000Hz");evidence.putString("live_provider_request","false");
            finish(-1,evidence);
        }catch(Throwable failure){evidence.putString("result","FAIL");evidence.putString("error",failure.toString());finish(1,evidence);}
        finally{if(original!=null)original.delete();if(compressed!=null&&!compressed.equals(original))compressed.delete();if(broken!=null)broken.delete();}
    }
    private static void le(OutputStream out,int value,int bytes)throws Exception{for(int n=0;n<bytes;n++)out.write((value >>> (n*8))&255);}
    private static byte[] digest(File file)throws Exception{MessageDigest hash=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[65536];try(FileInputStream in=new FileInputStream(file)){int n;while((n=in.read(buffer))!=-1)hash.update(buffer,0,n);}return hash.digest();}
    private static void check(boolean good,String message){if(!good)throw new AssertionError(message);}
}
