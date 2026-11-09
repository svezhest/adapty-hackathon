"""The 200 people behind the demo's tickets: invented, one line each, all different (scripts/dataset_cards.py).

Line: name | country | city | age | job | what is going on in their life | optional tag.
The tag says which complaints the circumstance suits: trial, cancel, access, double, refund, billing, unaware, credits.
Everyone here is made up. The circumstances are ordinary facts of how stores, banks and phones work.
"""

from __future__ import annotations

EN = """
Maya Thompson|US|Columbus|34|ER nurse on night shifts|checks her bank app at 4 a.m. on break and saw the charge there|trial
Darnell Brooks|US|Atlanta|41|MARTA bus driver|training for the Peachtree Road Race with a bad knee|
Priya Raman|US|Sunnyvale|29|QA engineer|moved from a Pixel to an iPhone through her carrier's upgrade deal|access
Tom Kowalczyk|US|Milwaukee|58|retired machinist|his daughter set the phone up and he never touched the App Store himself|unaware
Jess Alvarez|US|El Paso|26|dental hygienist|pays everything from a Chime account that had $11 left the day of the charge|trial
Hannah Weiss|US|Brooklyn|37|public school teacher|Family Sharing organizer for three kids, sees every receipt in one inbox|unaware
Marcus Webb|US|Detroit|45|UAW line worker|was on strike pay for weeks and went through every subscription on his statement|cancel
Kelsey Dunn|US|Boise|31|physical therapy assistant|recommends home workouts to patients and tried the app to see if it was any good|
Andre Fontaine|US|New Orleans|52|line cook|lost power for four days in a storm and missed the trial end date|trial
Sofia Marchetti|US|Providence|23|grad student on a stipend|has a spreadsheet of every recurring charge|double
Ben Okafor|US|Houston|39|petroleum engineer|travels offshore two weeks at a time with no signal|cancel
Lauren Pike|US|Minneapolis|44|tax preparer|noticed it while sorting statements for her accountant-brain year end check|double
Grace Lin|US|Seattle|36|UX designer, new mom|returning to exercise six months postpartum, phone always in one hand|
Rob Hensley|US|Knoxville|61|long haul trucker|does stretching at truck stops, reads everything on a cracked phone screen|cancel
Tiana Morris|US|Baltimore|28|pharmacy tech|Apple Card alert popped up in the middle of a shift|trial
Evan Schultz|US|Madison|19|college freshman|his parents' card is on the Apple ID and they asked him about it|unaware
Carmen Ruiz|US|San Antonio|47|school cafeteria manager|type 2 diagnosis, her doctor told her to walk more|
Will Nakamura|US|Honolulu|33|hotel front desk|time zone made the renewal land a day earlier than he thought|trial
Dana Fisher|US|Portland|50|freelance copy editor|reads terms for a living and quotes them back|refund
Jamal Carter|US|Philadelphia|24|Amazon warehouse associate|overdraft fee hit because of the renewal|refund
Rachel Stein|US|Chicago|42|paralegal|uses precise language and keeps every email thread|refund
Nick Petrov|US|Sacramento|30|CAL FIRE seasonal crew|out on a fire assignment for three weeks|cancel
Abby Lawson|US|Tulsa|27|hair stylist|did a free trial with a coworker as a bet|trial
Greg Tanaka|US|San Diego|55|Navy veteran|VA physical therapy for a shoulder, on a fixed income|refund
Morgan Ellis|US|Denver|32|ski patrol in winter, landscaper in summer|income stops in shoulder season, cutting all subscriptions|cancel
Latoya Greene|US|Memphis|38|home health aide|shares one phone with her teenage son|unaware
Sean Murphy|US|Boston|48|union electrician|typed this with a thumb between jobs|
Isabel Cortez|US|Miami|35|flight attendant|switches between US and Spanish App Store accounts|double
Owen Larson|US|Fargo|22|ag equipment apprentice|prepaid Visa from a birthday gift ran out|billing
Nina Patel|US|Edison|40|pharmacist|new card number after a fraud alert from Chase|billing
Cody Bates|US|Lubbock|25|oilfield hand|phone replaced after falling in a mud pit, restored from nothing|access
Melissa Hart|US|Charlotte|46|bank teller|knows exactly how pending charges work and is annoyed anyway|
Jordan Reyes|US|Albuquerque|20|community college student|bought the plan for AI workout plans for his wrestling season|credits
Paula Dawson|US|Louisville|63|church secretary|grandson installed the app at Thanksgiving last year|unaware
Ian Whitfield|US|Burlington|29|brewery packaging lead|wants to run the Vermont City Marathon|credits
Stacy Kim|US|Irvine|33|orthodontic assistant|paid in the app and on the website after a Facebook ad|double
Luis Herrera|US|Fresno|43|farm labor contractor|writes in a hurry between crews, English is his second language|
Brooke Sanders|US|Nashville|27|session musician|gig income, watches every dollar in Monzo-style budgeting app YNAB|cancel
Terrence Cole|US|Cleveland|36|RTA mechanic|deleted the app when he got a Peloton from a neighbor|cancel
Amy Sorensen|US|Salt Lake City|39|mom of four|Screen Time and Ask to Buy are on for all the kids, except apparently not|unaware
Oliver Grant|GB|Leeds|35|NHS physio|tells patients to use apps, now embarrassed|
Sophie Bennett|GB|Bristol|27|junior doctor on rotation|night shifts, reads messages hours later|trial
Callum Fraser|GB|Glasgow|31|ScotRail conductor|Monzo notification came through during a shift|trial
Amara Osei|GB|Peckham|24|barista and art student|overdrawn on Starling after the renewal|refund
Harriet Cole|GB|York|59|retired headteacher|writes full formal letters, cites the Consumer Rights Act|refund
Jake Turner|GB|Cardiff|22|Deliveroo rider|training for the Cardiff Half on his days off|
Fiona Walsh|GB|Belfast|43|care home manager|uses a work phone and a personal phone with different Apple IDs|access
Ravi Shah|GB|Leicester|38|optician|two subscriptions on one Barclaycard statement|double
Emily Hughes|GB|Swansea|29|primary teacher|cancelled during half term, charged anyway|cancel
Dan Pearce|GB|Sheffield|47|HGV driver|Android at home, iPhone from work|access
Zoe Clarke|GB|Norwich|33|veterinary nurse|on maternity leave, statutory pay only|cancel
Ahmed Hussain|GB|Bradford|26|taxi driver|keeps fit around Ramadan fasting hours|trial
Lucy Grant|GB|Brighton|30|freelance illustrator|signed up during Couch to 5K and forgot|trial
Mark Dobson|GB|Newcastle|52|Nissan plant worker|wanted to get in shape for the Great North Run|cancel
Chloe Adeyemi|GB|Manchester|21|uni student|Student Beans discount promised nothing, price looked different|trial
Peter Lloyd|GB|Exeter|66|retired postman|hearing about it from his bank's fraud team first|unaware
Tasha Reid|GB|Liverpool|28|hospital porter|renewed the week rent went out|refund
Greg Fenwick|GB|Oxford|41|bike shop owner|very dry sense of humour about being charged|
Bethan Rees|GB|Aberystwyth|36|farmer's wife and bookkeeper|patchy rural signal, restore never finishes|access
Imran Malik|GB|Birmingham|44|pharmacy owner|asked for a VAT receipt and got nowhere|
Chloe Tremblay|CA|Montreal|32|French immersion teacher|bilingual, sees the charge in CAD on a Desjardins statement|
Ethan MacLeod|CA|Halifax|27|ferry deckhand|two weeks on, two off, misses every deadline|trial
Simran Gill|CA|Brampton|30|logistics coordinator|Tangerine card replaced after it was skimmed at a gas station|billing
Noah Bouchard|CA|Quebec City|45|snowplow operator|only opens the phone at the end of a shift|cancel
Jenna Wong|CA|Vancouver|38|physiotherapist|price in CAD did not match the price she remembered|trial
Liam Fraser|CA|Calgary|24|apprentice welder|Tim's run conversation convinced him to try the app|unaware
Meg Sinclair|CA|Winnipeg|55|Canada Post carrier|winter walking route, wants indoor plans|
Tyler Hunt|CA|Toronto|29|TTC operator|bought on the web from an Instagram ad and in the app too|double
Aroha Ngata|NZ|Rotorua|33|tourism guide|phone switched from iPhone to Samsung|access
Hamish Reid|NZ|Dunedin|26|physio student|paying in NZD through a student account with no overdraft|refund
Kiri Thompson|NZ|Wellington|41|public servant|wrote the ticket in her lunch break, very orderly|
Liam O'Connor|IE|Cork|29|Apple Cork campus contractor|knows how the system should work and is curious why it does not|access
Aoife Byrne|IE|Galway|34|secondary teacher|Revolut notification, then the same charge again the next week|double
Sean Daly|IE|Dublin|48|Dublin Bus driver|told his daughter to sort it, she wrote half of this|
Niamh Kelly|IE|Limerick|25|nursing student|wants the EU 14 day withdrawal explained|refund
Lachlan Boyd|AU|Brisbane|31|FIFO mine worker|three weeks on site with no decent reception|cancel
Mia Nguyen|AU|Melbourne|28|nail salon owner|heard ACCC mentioned on the radio and brings it up|refund
Bronwyn Hayes|AU|Perth|54|school bus driver|paying with an old card through Afterpay-era habits|billing
Jack Sullivan|AU|Sydney|36|paramedic|ABN receipts matter for his tax return|
Tegan Walsh|AU|Adelaide|23|barista|saw AU$ price much higher than the US ad she clicked|trial
Dylan Crowe|AU|Hobart|40|boat builder|barely uses apps, one line answers|cancel
Lukas Hoffmann|DE|Munich|33|BMW process engineer|N26 card, writes tidy numbered lists|refund
Anna Schneider|DE|Hamburg|28|Hafen logistics clerk|cites Widerrufsrecht in careful English|refund
Felix Braun|DE|Cologne|24|Deutsche Bahn trainee|trains late, tried cancelling on the train with no connection|cancel
Katrin Vogel|DE|Leipzig|46|midwife|called three times, gave up, writes now|
Jonas Keller|DE|Stuttgart|37|Bosch technician|Sparkasse statement shows only APPLE.COM/BILL and nothing else|unaware
Mareike Lang|DE|Berlin|30|startup office manager|PayPal is the payment method in Google Play|cancel
Tobias Ernst|DE|Dresden|52|tram driver|wants everything in writing, doesn't trust chats|
Lena Wolff|DE|Freiburg|26|medical student|Black Forest trail running plan disappeared after reinstall|access
Stefan Maier|DE|Nuremberg|41|accountant|asks for a proper invoice with VAT|double
Julia Krause|DE|Bremen|35|ship insurance broker|Apple gift card credit got used up instead of the card|unaware
Pim de Vries|NL|Utrecht|29|NS train dispatcher|paid with iDEAL through the web checkout|double
Sanne Bakker|NL|Rotterdam|34|district nurse|bikes everywhere, wants to add strength work|trial
Daan Visser|NL|Groningen|22|student|ING app alert while on Lowlands weekend|trial
Marieke Smit|NL|Amsterdam|43|museum educator|very polite, very firm|refund
Bram Jansen|NL|Eindhoven|38|ASML cleanroom tech|work phone cannot install anything, personal phone is old|access
Camille Laurent|FR|Lyon|31|SNCF controller|writes English with French habits, misses accents|cancel
Hugo Moreau|FR|Toulouse|27|Airbus intern|prepaid Lydia card with limited balance|billing
Elodie Girard|FR|Nantes|39|pediatric nurse|loi Chatel mentioned, a friend told her|cancel
Antoine Roux|FR|Marseille|50|port worker|his son translated this for him|
Lea Fournier|FR|Lille|24|fine arts student|Boursorama statement and an apology for her English|trial
Mathieu Blanc|FR|Grenoble|44|ski instructor|bought lifetime in summer, then the weekly kept going|double
Carlos Ortega|ES|Valencia|35|Mercadona shift lead|Spain-wide blackout day messed up his purchase confirmation|access
Lucia Navarro|ES|Seville|28|flamenco school admin|Bizum person, not used to card subscriptions|unaware
Javier Prieto|ES|Bilbao|46|Athletic stadium security|writes short, uses exclamation marks|
Nuria Vidal|ES|Barcelona|33|tour guide|summer season ended, cutting spending|cancel
Marco Bianchi|IT|Turin|40|Fiat line engineer|PostePay card reloaded only when he needs it|billing
Giulia Conti|IT|Bologna|26|physics PhD|spotted the math on the price difference immediately|trial
Paolo Greco|IT|Naples|57|pizzeria owner|his niece set it all up|unaware
Elin Lindqvist|SE|Gothenburg|32|Volvo designer|Swish user, reads the receipt in SEK|
Oskar Berg|SE|Umea|25|ski coach|moved phone to Android for a season|access
Karin Holm|SE|Stockholm|47|school principal|writes neat formal sentences|refund
Kasia Nowak|PL|Warsaw|29|PKO call centre agent|knows what support scripts sound like|
Tomasz Zielinski|PL|Gdansk|37|shipyard welder|BLIK at home but card on Google Play|billing
Agnieszka Wrona|PL|Krakow|31|tour guide|paid in PLN, the ad showed euros|trial
Marek Kaczmarek|PL|Poznan|53|bus depot manager|wrote it twice because the first form vanished|
Arjun Mehta|IN|Bengaluru|27|Infosys developer|RBI e-mandate rule means his bank should have asked first, he thought|trial
Sneha Kulkarni|IN|Pune|24|MBA student|UPI autopay shows in PhonePe and she never approved it, she thinks|unaware
Rohit Sharma|IN|Delhi|33|Zomato operations|charged in rupees double what a friend pays|trial
Divya Nair|IN|Kochi|30|Gulf returnee nurse|her Google Play country is still UAE|double
Karthik Iyer|IN|Chennai|41|CA, chartered accountant|asks for GST invoice|
Pooja Verma|IN|Lucknow|22|government exam aspirant|every rupee counts, writes respectfully, "kindly"|refund
Vikram Singh|IN|Jaipur|36|hotel manager|uses Hinglish phrasing in English sentences|cancel
Ananya Das|IN|Kolkata|26|school teacher|Jio phone upgrade lost the Play purchase|access
Manoj Pillai|IN|Mumbai|48|local train commuter|types on the train, long sentences with no stops|credits
Neha Joshi|IN|Ahmedabad|29|textile exporter's daughter|used credits for AI meal plan, got an error|credits
Sanjay Rao|IN|Hyderabad|38|pharma QA lead|attached transaction ID in his very first line|double
Farah Khan|IN|Bhopal|31|physiotherapist|wants to recommend the app to patients if this is fixed|
Rafael Souza|BR|Sao Paulo|30|iFood rider|Nubank alert, charged in reais|trial
Camila Rocha|BR|Recife|26|nurse technician|writes warm, then angry in the same paragraph|refund
Thiago Lima|BR|Curitiba|41|Renault plant worker|Google Play balance from a promotion got used|unaware
Beatriz Alves|BR|Belo Horizonte|35|lawyer|mentions Procon as a next step|refund
Gustavo Pereira|BR|Porto Alegre|23|engineering student|bought AI coach credits and the generation failed|credits
Diego Ramirez|MX|Guadalajara|33|Telcel store clerk|Spanglish, carrier billing on Android|billing
Valeria Soto|MX|Monterrey|28|plant QA|bought during Buen Fin deals, price changed after|trial
Fernando Cruz|MX|Mexico City|45|Metro technician|mentions Profeco politely|refund
Ximena Vega|MX|Puebla|24|med student|OXXO prepaid card, ran out of balance|billing
Thabo Mokoena|ZA|Johannesburg|34|Gautrain technician|load shedding days killed his routine|cancel
Lerato Dlamini|ZA|Cape Town|27|tour operator|Capitec notification, rand price surprised her|trial
Pieter van Wyk|ZA|Pretoria|51|engineer|Comrades Marathon goal, straight talking|
Nomsa Khumalo|ZA|Durban|39|clinic sister|worried about every rand with school fees due|refund
Chidi Eze|NG|Lagos|29|fintech analyst|naira card declined on App Store, tried three banks|billing
Amina Bello|NG|Abuja|33|NGO coordinator|dollar card limit from her bank was the problem|billing
Tunde Afolabi|NG|Ibadan|25|medical student|paid through a friend's card abroad, app shows nothing|access
Maria Santos|PH|Quezon City|31|BPO night shift agent|writes like she handles tickets for a living|
Jun Reyes|PH|Cebu|27|seafarer between contracts|Maya wallet, comes back to land and sees charges|cancel
Angel Dela Cruz|PH|Davao|23|nursing board reviewee|GCash linked Play account, wants a small refund|refund
Rico Bautista|PH|Manila|40|OFW just home from Dubai|his App Store country is still UAE|double
Wei Ling Tan|SG|Singapore|35|DBS operations|PayLah user, cannot stand vague answers|
Marcus Lim|SG|Singapore|28|national service reservist|in camp for two weeks, no phone|cancel
Yousef Haddad|AE|Dubai|37|Emirates cabin crew|buys on layovers, store country confusion|double
Layla Mansour|AE|Abu Dhabi|29|clinic receptionist|Ramadan schedule made her forget the renewal|trial
Emre Yilmaz|TR|Istanbul|31|metro engineer|lira moved so much the price makes no sense now|trial
Zeynep Kaya|TR|Izmir|26|English teacher|very correct English, one small irony|
Hiro Sato|JP|Osaka|42|JR station staff|extremely polite, numbered timeline, one quiet complaint|refund
Min-ji Park|KR|Seoul|28|Samsung QA|sends a screenshot description and exact error text|access
Ingrid Haugen|NO|Bergen|38|offshore catering|two weeks on the rig, Vipps at home|cancel
Tiago Ferreira|PT|Lisbon|34|Carris tram mechanic|MB Way user, blackout day purchase got lost|access
Eva Novak|CZ|Prague|27|tram dispatcher|PPL parcel-style exactness about dates|
Ruth Abrams|US|Pittsburgh|68|retired librarian|keeps paper receipts in a folder and looks things up before she writes|refund
"""

RU = """
Анна Соколова|RS|Белград|33|дизайнер, переехала из Москвы в 2022|карта Raiffeisen Srbija, Apple ID сменила на сербский регион, и подписка потерялась|access
Дмитрий Власов|GE|Тбилиси|38|бэкендер на удалёнке|карта Bank of Georgia, цена в лари на выписке не совпала с ожиданием|trial
Ольга Петренко|KZ|Алматы|41|бухгалтер|карта Kaspi, считает всё до тенге и просит чек|double
Игорь Смирнов|AM|Ереван|29|геймдизайнер|карта Ardshinbank выпущена за день, с неё и списали|trial
Марина Кузнецова|DE|Берлин|47|медсестра, живёт в Германии двадцать лет|пишет с немецкими оборотами, упоминает Widerrufsrecht|refund
Павел Орлов|RS|Нови-Сад|35|инженер на удалёнке|отменял с телефона на кофе в центре, думал, что отменилось|cancel
Екатерина Волкова|LV|Рига|52|преподаватель|пишет длинно и вежливо, как письмо в учреждение|cancel
Сергей Морозов|IL|Хайфа|44|техник в больнице|смена часового пояса в Apple ID и странные даты продления|cancel
Алина Гринберг|CY|Лимасол|27|саппорт в форекс-компании|сама работает в поддержке и видит шаблоны насквозь|refund
Никита Лебедев|TR|Анталья|31|фрилансер|карта турецкого банка, лира, цена поменялась прямо между списаниями|trial
Татьяна Белова|KZ|Астана|58|учитель на пенсии|всё установил сын, она только нажимала «продолжить»|unaware
Роман Гусев|RS|Белград|26|курьер Wolt|бегает по Калемегдану, купил годовой и пропала подписка после переустановки|access
Ирина Шевченко|PL|Варшава|36|логист|карта Revolut, оплатила и на сайте, и в приложении|double
Максим Фролов|UZ|Ташкент|24|студент|карта Humo не проходит, платил через карту друга|billing
Елена Тарасова|ME|Бар|43|сдаёт апартаменты|сезон закончился, режет подписки|cancel
Артём Киселёв|AE|Дубай|30|менеджер в логистике|покупал на аккаунте другого региона, платный доступ не открылся|access
Светлана Романова|FI|Хельсинки|39|врач|пишет сухо, по пунктам, раздражена один раз за всё письмо|refund
Глеб Сафронов|GE|Батуми|28|бариста|купил кредиты для AI-плана, генерация упала, кредиты сгорели|credits
Вера Николаева|RS|Земун|61|пенсионерка|внучка оформила пробный период, теперь списания, пишет с голосового ввода|unaware
Константин Рябов|TH|Пхукет|37|инструктор по дайвингу|связь на лодке пропадает, отменял в последний день|cancel
"""

SR = """
Milica Jovanović|RS|Beograd|31|medicinska sestra u KBC Zvezdara|noćne smene, videla skidanje u Raiffeisen aplikaciji u pet ujutru|trial
Nikola Petrović|RS|Novi Sad|27|programer u Vojvodini|Banca Intesa kartica, uzeo pretplatu zbog Novosadskog polumaratona|cancel
Jelena Stojanović|RS|Niš|44|profesorka u gimnaziji|piše gramatički savršeno, svaku rečenicu do kraja|refund
Marko Ilić|RS|Kragujevac|36|radnik u Stellantisu|dinari mu ne štimaju sa cenom koju je video|trial
Ana Đorđević|RS|Beograd|24|studentkinja FON-a|nema pretplatu kod mobilnog operatera, a Yettel račun je veći|unaware
Stefan Nikolić|RS|Subotica|29|vozač u Nexe grupi|bez dijakritika, piše s telefona u kamionu|cancel
Dragana Marković|RS|Čačak|52|računovođa|traži račun i precizan datum, sve poredi s izvodom iz NLB Komercijalne|double
Luka Savić|RS|Beograd|22|barmen na Dorćolu|žargon, bez velikih slova, trenirao za Beogradski maraton|cancel
Tamara Pavlović|RS|Zrenjanin|38|apotekarka|ćerka je na njenom telefonu kliknula na ponudu|unaware
Vladimir Kostić|RS|Valjevo|47|inženjer u Krušiku|ćirilica u svemu, i u Viberu i u poruci podršci|refund
Ivana Lukić|RS|Pančevo|33|knjigovođa|Poštanska štedionica, izvod stigao poštom pa tek tad videla|trial
Miloš Radovanović|RS|Beograd|41|taksista|piše dok čeka vožnju na aerodromu, kratko i ljutito|refund
Sara Tomić|RS|Novi Sad|26|fizioterapeut|preporučuje pacijentima vežbe, promenila telefon na iPhone|access
Aleksandar Vuković|RS|Kraljevo|55|mašinbravar|sin mu je podesio, nikad nije video App Store|unaware
Jovana Ristić|RS|Beograd|30|PR u agenciji|kupila i na sajtu i u aplikaciji posle reklame na Instagramu|double
Bojan Simić|RS|Šabac|34|poljoprivrednik|OTP kartica istekla, ostao bez pristupa usred plana|billing
Katarina Živković|RS|Vršac|28|vinarka|platila kredite za AI plan ishrane, generisanje puklo|credits
Nemanja Popović|RS|Beograd|25|dostavljač Glova|ćirilica ponekad, reinstalirao pa nestalo sve|access
Teodora Marinković|BA|Banja Luka|32|nastavnica|piše ijekavski, račun u markama, zbunjuje je iznos|trial
Filip Janković|ME|Podgorica|37|turistički vodič|sezona gotova, hoće da ugasi, ne nalazi gde|cancel
"""


def parse(block: str) -> list[dict]:
    people = []
    for line in block.strip().splitlines():
        name, country, city, age, job, life, tag = [part.strip() for part in line.split("|")]
        people.append({"name": name, "country": country, "city": city, "age": int(age), "job": job, "life": life, "tag": tag or None})
    return people


# How a person writes: each ticket gets one habit, and no two tickets share habit, tone and shape together.
HABITS = [
    "no capital letters at all and hardly any punctuation",
    "very long run-on sentences, like voice dictation",
    "one sentence per line",
    "writes dates exactly and in a numbered timeline",
    "starts in the middle of a thought, no greeting",
    "over-polite, apologises for bothering anyone",
    "dry, deadpan humour, one good joke",
    "ALL CAPS on one or two words only",
    "keeps asking short direct questions",
    "typos from a phone keyboard, left uncorrected",
    "uses a lot of brackets for side remarks",
    "tells a tiny story first, the problem comes last",
    "the problem first, then why it matters to them",
    "quotes the exact words of a bank alert or receipt",
    "formal, like a letter to an institution",
    "text-message abbreviations (idk, tbh, pls, rn)",
    "ellipses everywhere... trailing off...",
    "ends with a concrete deadline they set themselves",
    "compares this with how another company treated them",
    "second-language grammar, consistent and natural for their mother tongue",
    "lists what they already tried, with a dash per attempt",
    "calm and reasonable, says they like the app otherwise",
    "self-blaming: admits part of it was their own fault",
    "sighs on the page: tired, not angry",
    "writes as if explaining to a child what went wrong, a bit condescending",
    "one short paragraph, no fluff, signs with their initials",
    "mixes a word or two of their mother tongue into the text",
    "asks one thing, then adds a P.S. with the real question",
    "gives far too much irrelevant detail, the real point is buried",
    "very short: under fifteen words",
    "exact numbers everywhere: amounts, card endings, times",
    "exclamation marks when worried, none when calm",
]

TONES = [
    "furious", "calm", "tired", "confused", "embarrassed", "sarcastic", "polite but firm", "worried about money",
    "disappointed fan of the app", "business-like", "suspicious", "cheerful despite it",
]

SHAPES = [
    "opens with the money", "opens with the moment they noticed", "opens with who they are", "opens with a question",
    "opens with what they want done", "opens with what they tried", "opens with what happened to someone else in the household",
    "opens with a date", "opens with the app's screen, what it says", "opens by saying this is the second time they write",
]
