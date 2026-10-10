package com.majlis.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextDirectionHeuristics;
import android.text.TextPaint;

import java.io.OutputStream;
import java.util.Locale;

/** Exports the displayed report with shaped Arabic text in a real PDF. */
public final class MeetingPdf {
    private static final int PAGE_WIDTH = 595;
    private static final int PAGE_HEIGHT = 842;
    private static final int MARGIN = 42;
    private static final int FOOTER_HEIGHT = 30;

    private MeetingPdf() { }

    public static void write(Context context, String report, OutputStream output) throws Exception {
        if (context == null || output == null || report == null || report.trim().isEmpty()) {
            throw new Exception("لا يوجد محضر صالح لتصديره.");
        }
        TextPaint paint = new TextPaint(TextPaint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.rgb(25, 42, 49));
        paint.setTextSize(12);
        paint.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        StaticLayout layout = StaticLayout.Builder.obtain(report, 0, report.length(), paint,
                        PAGE_WIDTH - 2 * MARGIN)
                .setAlignment(Layout.Alignment.ALIGN_NORMAL)
                .setTextDirection(TextDirectionHeuristics.FIRSTSTRONG_RTL)
                .setLineSpacing(3, 1)
                .setIncludePad(false)
                .build();
        int firstLine = 0;
        int pageNumber = 0;
        int availableHeight = PAGE_HEIGHT - 2 * MARGIN - FOOTER_HEIGHT;
        PdfDocument document = new PdfDocument();
        try {
            while (firstLine < layout.getLineCount()) {
                if (Thread.currentThread().isInterrupted()) throw new Exception("أُلغي تصدير المحضر.");
                int top = layout.getLineTop(firstLine);
                int lastLine = firstLine;
                while (lastLine + 1 < layout.getLineCount()
                        && layout.getLineBottom(lastLine + 1) - top <= availableHeight) lastLine++;
                int height = Math.min(availableHeight, layout.getLineBottom(lastLine) - top);
                PdfDocument.Page page = document.startPage(new PdfDocument.PageInfo.Builder(
                        PAGE_WIDTH, PAGE_HEIGHT, ++pageNumber).create());
                Canvas canvas = page.getCanvas();
                canvas.drawColor(Color.WHITE);
                canvas.save();
                canvas.clipRect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, MARGIN + height);
                canvas.translate(MARGIN, MARGIN - top);
                layout.draw(canvas);
                canvas.restore();
                TextPaint footer = new TextPaint(paint);
                footer.setTextSize(9);
                footer.setColor(Color.GRAY);
                String number = String.format(Locale.ROOT, "%d", pageNumber);
                canvas.drawText(number, (PAGE_WIDTH - footer.measureText(number)) / 2,
                        PAGE_HEIGHT - MARGIN / 2f, footer);
                document.finishPage(page);
                firstLine = lastLine + 1;
            }
            document.writeTo(output);
            output.flush();
        } finally {
            document.close();
        }
    }
}
