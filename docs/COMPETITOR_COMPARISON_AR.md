# مجلس ومثيلاته العالمية

تاريخ مراجعة المصادر: **10 أكتوبر 2026، بتوقيت Asia/Hebron**.

«مجلس» نموذج أولي عربي للاجتماعات الحضورية على أندرويد. المقارنة أدناه تقابل **تنفيذ مجلس v0.2 الذي راجعناه في المصدر** بما توثقه الشركات رسميًا، وليست اختبار دقة صوتية أو ترتيبًا للأفضل. ميزات مجلس المذكورة منفذة في الشيفرة؛ قراءة المصدر لا تثبت نجاح البناء أو تشغيلها على هاتف. يوضح [دليل المشروع](../README.md) حالة الإصدار والتحقق منه.

## الكلام العربي وتمييز المتحدثين

| التطبيق | تفريغ الكلام العربي | التسجيل الحضوري | فصل الأصوات وربطها بالأسماء |
| --- | --- | --- | --- |
| **مجلس v0.2** | الطلب مضبوط للعربية؛ يحتاج إعداد خادم ومزود OpenAI | ميكروفون أندرويد، حفظ محلي، إشعار ظاهر، إيقاف مؤقت واستئناف؛ حتى 60 دقيقة فعلية | بعد انتهاء التسجيل: فصل المتحدثين ومطابقة حتى أربع عينات مسماة محفوظة محليًا. بقية الأصوات تُرقّم؛ يمكن تصحيح الاسم والمتحدث لكل مقطع. [التسجيل](../android/app/src/main/java/com/majlis/app/RecordingService.java)، [الواجهة](../android/app/src/main/java/com/majlis/app/MainActivity.java)، [الخادم](../backend/app/provider.py) |
| **Otter** | **العربية ليست ضمن اللغات الست المعلنة**؛ ترجمة نص إلى العربية لا تعني تفريغ صوت عربي. [اللغات][o-lang] | تطبيق أندرويد وiOS وتسجيل من الميكروفون. [الوظائف والخطط][o-price] | يتعلم الأصوات من التسميات السابقة. فصل الأصوات ومطابقتها في التسجيل العادي يبدأ بعد الإيقاف؛ التسمية الحية عبر Zoom تعتمد أسماء المشاركين. [التسمية][o-speaker] |
| **Fireflies** | العربية ضمن اللغات المدعومة؛ وضع عدة لغات في الاجتماع متاح في Business وEnterprise. [اللغات][f-lang]، [الوضع المتعدد][f-multi] | تطبيق أندرويد وiOS للاجتماعات الحضورية، مع إيقاف مؤقت واستئناف. [تطبيق الجوال][f-mobile] | الملفات المرفوعة تستخدم «Speaker 1» وغيرها؛ يوضح الدليل أن اكتشاف الاسم يعتمد اجتماع فيديو ينضم إليه المساعد أو إضافة Chrome. [حدود الأسماء][f-speaker] |
| **Notta** | العربية ضمن 58 لغة للتفريغ أحادي اللغة. [اللغات][n-lang] | تطبيق جوال، وتفريغ مباشر من الميكروفون. [الخطط][n-price] | **الفصل المباشر من الميكروفون موثق لليابانية فقط**. الملفات المرفوعة تدعم الفصل في جميع لغات التفريغ حتى 10 متحدثين عند تفعيله. أسماء الاجتماعات عبر Bot تعتمد حسابات المشاركين. [قيود التمييز][n-speaker] |
| **Plaud** | العربية ضمن 112+ لغة؛ الوثيقة تصف التفريغ أحادي اللغة لكل تسجيل. [اللغات][p-lang] | التجربة الشخصية تعتمد مسجل Plaud، ويستطيع التطبيق بدء التسجيل على الجهاز المتصل. [طريقة التسجيل][p-record]، [احتياج الجهاز][p-device] | فصل متحدثين مع تعديل الأسماء؛ توجد أيضًا ملفات صوتية وتعلّم من التسميات ومزامنتها للاجتماعات التالية، بغض النظر عن لغة الكلام. [الأسماء][p-speaker]، [تعلّم الأصوات][p-voice] |

**الاسم الحقيقي لا ينتج من فصل الأصوات وحده.** هناك فرق بين «متحدث 1»، ومطابقة عينة سبق أن سماها المستخدم، وقراءة اسم حساب مشارك في اجتماع فيديو. مجلس يستخدم الخيار الثاني عند نجاح المطابقة، ولا يقدم مصادقة بيومترية. **v0.2 لا يوفر تفريغًا أو تسمية أصوات لحظية.** انضمام شخص لاحقًا يعني إمكان فصل صوته إذا تكلم والتقطه التسجيل؛ لا يعني اكتشاف دخوله الغرفة أو معرفة اسمه لحظيًا. [عقد مجلس](../backend/README.md)، [توثيق Otter][o-speaker]، [توثيق Notta][n-speaker].

## الملفات والاتصال والتكلفة

| التطبيق | استيراد وتصدير | ماذا يعمل دون إنترنت؟ | نموذج التكلفة |
| --- | --- | --- | --- |
| **مجلس v0.2** | استيراد M4A/MP3/WAV حتى 24 مليون بايت و60 دقيقة؛ تصدير PDF/JSON/TXT ومشاركة نص. لا يوجد SRT أو DOCX بعد. [الواجهة](../android/app/src/main/java/com/majlis/app/MainActivity.java)، [فحص الوسائط](../backend/app/media.py) | التسجيل، الاستيراد، العينات، البحث والتحرير والتصدير للمحاضر المحفوظة، والمثال التجريبي. التحليل يحتاج اتصالًا وتأكيد رفع الصوت والعينات؛ إعادة التلخيص تحتاج تأكيد رفع النص والأسماء فقط. [مسارات الواجهة](../android/app/src/main/java/com/majlis/app/MainActivity.java) | لا اشتراك مبرمج داخل النموذج؛ يلزم أن يضبط صاحبه خادمًا ورمز وصول ومفتاح المزود، ويتحمل الاستضافة والاستهلاك. لا يوجد خادم عام جاهز ضمن المشروع. [دليل الخدمة](../backend/README.md) |
| **Otter** | استيراد صوت/فيديو من الهاتف؛ TXT في Basic، وصيغ أخرى مثل PDF وDOCX وSRT في الخطط المدفوعة. [الاستيراد][o-import]، [التصدير][o-export] | يستمر التقاط الصوت عند انقطاع الشبكة؛ التفريغ يتوقف ثم يعالج الصوت بعد الاتصال. يلزم إبقاء التطبيق مفتوحًا حتى اكتمال الرفع وفق إرشادات الشركة. [التسجيل دون اتصال][o-offline] | Basic مجاني؛ Pro وBusiness وEnterprise باشتراك وحدود تختلف حسب الخطة. [الخطط الحالية][o-price] |
| **Fireflies** | استيراد MP3/MP4/M4A/WAV من الهاتف؛ تنزيل النص والمحضر بصيغ عدة في Pro فأعلى. [الاستيراد][f-import]، [التصدير][f-export] | يسجل محليًا؛ يختار المستخدم رفع الملفات غير المعالجة عند عودة الاتصال. [الملفات المحلية][f-offline] | Free وPro وBusiness وEnterprise؛ تختلف الملخصات والتخزين والميزات. [الخطط الحالية][f-price] |
| **Notta** | استيراد تسجيل خارجي عند الاتصال؛ تنزيل TXT وDOCX وSRT وPDF وXLSX في Pro فأعلى، مع قيود لبعض وظائف الجوال. [الاستيراد والاتصال][n-offline]، [التصدير][n-export] | تطبيق الجوال يحتاج الشبكة للتسجيل والتفريغ حسب الدليل. يوجد Privacy Mode على **الحاسوب**؛ اللغات المحلية المعلنة فيه لا تشمل العربية. [الجوال][n-offline]، [الوضع المحلي][n-local] | Free وPro وBusiness وEnterprise؛ العتاد Notta Memo خيار منفصل. [الخطط الحالية][n-price] |
| **Plaud** | استيراد ملفات إلى التطبيق؛ نص TXT/SRT/DOCX/PDF، وملخص TXT/Markdown/DOCX/PDF. [الاستيراد][p-import]، [التصدير][p-export] | مسجل NotePin S يحفظ الصوت محليًا؛ المعالجة والملخص يحتاجان اتصالًا. [التسجيل المحلي][p-offline] | شراء جهاز في التجربة الشخصية، وخطط Starter وPro وUnlimited. خطة Team تسمح بالمعالجة والاستيراد دون جهاز؛ التسجيل حينها من Desktop. [العضويات][p-price]، [استثناء Team][p-team] |

لم نثبت أسعارًا رقمية لأن البلد والفوترة والعروض تغير المقارنة؛ روابط الخطط تعرض المبلغ وحدوده وقت الشراء. لم نجرب المنافسين على التسجيل نفسه، ولم نقس دقة مجلس على هاتف أو في غرفة فعلية؛ لذلك لا توجد نسبة دقة أو ادعاء تفوق هنا.

## ما تغير في مجلس v0.2

أغلق التنفيذ الجديد عدة فجوات كانت في v0.1، وفق الملفات المحلية التالية:

- **جلسات أطول وإيقاف مؤقت:** حد 60 دقيقة من التسجيل الفعلي، وترميز AAC بمعدل 48 كيلوبت/ثانية؛ وقت الإيقاف المؤقت لا يدخل في العداد. هذا تغيير إعدادات وتنفيذ، ولا يثبت جودة الصوت عند المسافات البعيدة. [خدمة التسجيل](../android/app/src/main/java/com/majlis/app/RecordingService.java).
- **ملفات وقراءة أفضل:** استيراد ثلاث صيغ وحفظها محليًا أولًا؛ بحث في المحاضر والنص والمتحدثين؛ تشغيل التسجيل والانتقال إلى وقت المقطع أو دليل القرار/المهمة. الاستماع يبدأ من الوقت المحدد ولا يقتصر تلقائيًا على نهاية المقطع. [الواجهة](../android/app/src/main/java/com/majlis/app/MainActivity.java)، [البحث المحلي](../android/app/src/main/java/com/majlis/app/MeetingStore.java).
- **تصحيح وإعادة تلخيص:** تعديل النص والمتحدث لكل مقطع يحفظ التوقيت والأدلة ويظهر أن المحضر يحتاج تحديثًا. يمكن تعديل فقرة الملخص يدويًا أو طلب محضر جديد للنص المصحح بعد موافقة صريحة على إرساله مع الأسماء، دون إعادة إرسال الصوت. إعادة التلخيص تستبدل القرارات والمهام وتعيد حالات إنجازها كما يوضح التأكيد. [التحرير](../android/app/src/main/java/com/majlis/app/MeetingEdits.java)، [طلب النص](../android/app/src/main/java/com/majlis/app/MeetingApi.java)، [الخادم](../backend/app/main.py).
- **متابعة وتصدير:** علامات إنجاز محلية للمهام؛ تصدير PDF وJSON وTXT. لا يصدّر JSON رابط الصوت المحلي أو رمز الخدمة. [الواجهة](../android/app/src/main/java/com/majlis/app/MainActivity.java)، [مولد PDF](../android/app/src/main/java/com/majlis/app/MeetingPdf.java).
- **فحص الاتصال:** يختبر الخادم ورمز الوصول ويعرض الإصدار والحدود دون إرسال صوت. هذا فحص خدمة مجلس، وليس اختبار رصيد المزود أو توفر النموذج أو دقته. [عقد الحالة](../backend/app/main.py)، [عميل الخدمة](../android/app/src/main/java/com/majlis/app/MeetingApi.java).

## الأولويات التالية

1. **قياس العربية والتشغيل على هاتف قبل الادعاء بالتفوق:** تسجيلات وافق أصحابها على استخدامها، لهجات ومسافات وضجيج وتداخل ومتحدث يصل لاحقًا؛ قياس أخطاء التفريغ وإسناد الكلام وصحة القرارات والمهام وزمن وتكلفة المعالجة. تحقق أيضًا من ساعة تسجيل فعلية والإيقاف المؤقت وقفل الشاشة، وجودة PDF العربي. لا تدعم قراءة المصدر ادعاء «يسمع البعيد». [حدود مجلس](../backend/README.md).
2. **خدمة تحليل قابلة للتشغيل بسهولة:** نشر خادم يضبطه صاحبه، وضبط التكلفة وحدود الاستخدام، وحالة معالجة تستمر عند انقطاع الاتصال أو إعادة فتح التطبيق. فحص الحالة الحالي لا يغني عن تشغيل المزود الحقيقي. [فحص مجلس](../backend/app/main.py)، [مسار الملفات غير المعالجة في Fireflies][f-offline].
3. **إكمال مسار المراجعة:** تصحيح القرارات والمهام والمسؤولين والمواعيد مباشرة، وحالة اعتماد للمحضر، وحفظ نسخ التعديلات قبل إعادة التلخيص. v0.2 يحرر المقاطع وفقرة الملخص ويعلم المحضر القديم؛ لا يقدم دورة اعتماد أو تاريخ تعديلات بعد. [الواجهة الحالية](../android/app/src/main/java/com/majlis/app/MainActivity.java)، [تحرير المنافسين][n-price].
4. **توسيع الملفات والعمل الجماعي عند الحاجة:** SRT وDOCX/Markdown، ومشاركة محضر قابلة للضبط، ثم تكامل التقويم والمهام. PDF/JSON/TXT أصبحت منفذة بالفعل؛ صيغ المنافسين والتكاملات موثقة في [Otter][o-export]، [Fireflies][f-export]، [Notta][n-price]، [Plaud][p-export].
5. **التفريغ الحي ومعالجة الاجتماعات الأكبر:** يحتاجان تصميمًا منفصلًا للبث والتكلفة وتوحيد معرفات المتحدثين عبر المقاطع. v0.2 يحلل التسجيل كاملًا بعد الإيقاف، ويحافظ على حد 60 دقيقة و24 مليون بايت؛ لا ينبغي تسويق البث الحي أو معالجة ملفات بلا حدود كميزات متاحة. [عميل مجلس](../android/app/src/main/java/com/majlis/app/MeetingApi.java)، [الخادم](../backend/app/main.py).

مجلس يوفر أساسًا عربيًا محلي الحفظ مع خادم يضبطه صاحبه وتأكيد إرسال واضح. أما المنتجات التجارية فلديها ملفات وتعاون وتكاملات ومسارات تشغيل أوسع. وجود العربية وواجهة عربية في مجلس مفيد لهذا الاستخدام، لكنه لا يثبت وحده أن جودة تفريغه أو تمييزه للأصوات أفضل.

[o-lang]: https://help.otter.ai/hc/en-us/articles/360047247414-Supported-languages
[o-speaker]: https://help.otter.ai/hc/en-us/articles/360048465453-Tagging-speaker-names-in-a-conversation
[o-price]: https://otter.ai/pricing
[o-import]: https://help.otter.ai/hc/en-us/articles/360047733574-Import-an-audio-or-video-file
[o-export]: https://help.otter.ai/hc/en-us/articles/360047733634-Export-conversations
[o-offline]: https://help.otter.ai/hc/en-us/articles/44008744581271-Record-with-Otter-offline-or-without-an-internet-connection
[f-lang]: https://guide.fireflies.ai/articles/2973706448-learn-about-fireflies-supported-languages
[f-multi]: https://guide.fireflies.ai/articles/2585231364-transcribe-fireflies-meetings-in-multiple-languages-with-multi-language-mode-beta
[f-speaker]: https://guide.fireflies.ai/articles/9554534786-how-fireflies-joins-and-records-your-meetings-faqs
[f-mobile]: https://guide.fireflies.ai/articles/8937818258-learn-about-fireflies-mobile-app
[f-import]: https://guide.fireflies.ai/articles/9497861941-how-to-upload-and-transcribe-audio-video-files-in-the-fireflies-mobile-app
[f-export]: https://guide.fireflies.ai/articles/3319752033-how-to-download-transcripts-summaries-and-meeting-recordings-from-fireflies
[f-offline]: https://guide.fireflies.ai/articles/1360888790-how-to-upload-unprocessed-files-in-the-fireflies-mobile-app
[f-price]: https://fireflies.ai/pricing
[n-lang]: https://support.notta.ai/hc/en-us/articles/4403155631131-What-languages-does-Notta-support
[n-speaker]: https://support.notta.ai/hc/en-us/articles/4403163792027-Does-Notta-support-speaker-identification
[n-offline]: https://support.notta.ai/hc/en-us/articles/37293457169051-Can-I-record-and-transcribe-using-the-Notta-mobile-app-offline
[n-export]: https://support.notta.ai/hc/en-us/articles/15289070397851-Download-transcription-file
[n-local]: https://support.notta.ai/hc/en-us/articles/52683417314587-What-is-Privacy-Mode-Recording
[n-price]: https://www.notta.ai/en/pricing
[p-lang]: https://support.plaud.ai/hc/en-us/articles/53422487420953-How-many-languages-does-Plaud-support-for-transcription
[p-record]: https://support.plaud.ai/hc/en-us/articles/50594418855321-Record-through-the-Plaud-App
[p-device]: https://support.plaud.ai/hc/en-us/articles/60899330078489-Can-I-use-the-Plaud-App-without-a-Plaud-device
[p-speaker]: https://support.plaud.ai/hc/en-us/articles/50635937755161-Name-Speakers
[p-voice]: https://support.plaud.ai/hc/en-us/articles/54934400679065-Auto-speaker-labeling
[p-import]: https://support.plaud.ai/hc/en-us/articles/50609466994713-Audio-import
[p-export]: https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-recordings-transcripts-and-summaries
[p-offline]: https://support.plaud.ai/hc/en-us/articles/53771056805785-Does-Plaud-NotePin-S-work-offline
[p-price]: https://www.plaud.ai/pages/plaud-ai-plan-pricing
[p-team]: https://support.plaud.ai/hc/en-us/articles/57466295856537-What-is-Plaud-Team
