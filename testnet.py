import time
import os
import csv
import hmac
import hashlib
import logging
from datetime import datetime
import requests
from playwright.sync_api import sync_playwright

# 🔇 Asyncio ve Playwright içsel çalkantı uyarılarını terminalde susturur
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# ==========================================
# 📂 YEREL .ENV DOSYASI OKUMA MOTORU
# ==========================================
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir: continue
            anahtar, deger = satir.split("=", 1)
            os.environ[anahtar.strip()] = deger.strip().strip('"').strip("'")
    print("✅ .env dosyasındaki tüm anahtarlar hafızaya başarıyla yüklendi.")
else:
    print("⚠️ UYARI: Klasörde .env dosyası bulunamadı!")

# ==========================================
# ⚙️ GLOBAL AYARLAR VE DATA GATHERING AYARLARI
# ==========================================
KARA_LISTE = {"USDC", "USDT", "BUSD", "FDUSD", "AMD", "MSFT", "BABA", "SOXL", "NVDA", "AAPL", "COMP"}
TARAMA_ARALIGI = 300    # 5 Dakika (Saniye cinsinden)

# 💰 RISK VE KALDIRAÇ AYARLARI
POZISYON_MARJIN = 100.0   
KALDIRAC = 20            

# 📢 BİLDİRİM VE API BAĞLANTILARI
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
SECRET_KEY = os.getenv("BINANCE_TESTNET_SECRET_KEY")

# 🏛️ TESTNET ADRESİ
BASE_URL = "https://testnet.binancefuture.com"

aktif_pozisyonlar = {}

# ==========================================
# 🔐 BINANCE API İMZALAMA VE EMİR MOTORU
# ==========================================
def binance_imzali_talep(metot, uç_nokta, parametreler=None):
    if not API_KEY or not SECRET_KEY:
        print("❌ [API HATASI] .env dosyasından API_KEY veya SECRET_KEY okunamadı!")
        return None
        
    params = parametreler.copy() if parametreler else {}
    params["timestamp"] = int(time.time() * 1000)
    
    # Parametreleri query string formatına getirip imzalıyoruz
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(SECRET_KEY.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["signature"] = signature
    
    headers = {"X-MBX-APIKEY": API_KEY}
    url = f"{BASE_URL}{uç_nokta}"

    try:
        if metot.upper() == "GET":
            res = requests.get(url, params=params, headers=headers, timeout=10)
        elif metot.upper() == "POST":
            res = requests.post(url, params=params, headers=headers, timeout=10)
        else:
            print(f"❌ [METOT HATASI] Geçersiz HTTP metodu: {metot}")
            return None

        # ⚠️ 200 (OK) veya 202 (Accepted) dışındaki durumlar hata kabul edilir
        if res.status_code not in [200, 202]:
            print(f"⚠️ [BORSA REDDİ] Sunucu Kod Döndü: {res.status_code} | Yanıt: {res.text}")

        # 🛠️ KESİN ÇÖZÜM: Boş veya JSON dışı yanıtları (HTTP 202 gibi) güvenle karşılayan alan
        try:
            if not res.text or res.text.strip() == "":
                return {"status_code": res.status_code, "msg": "Empty response from server"}
            return res.json()
        except ValueError:
            # Eğer yanıt JSON formatında değilse (düz metin veya boşsa) çökme, sözlük olarak dön
            return {"status_code": res.status_code, "raw_response": res.text}

    except Exception as e:
        print(f"❌ [BAĞLANTI HATASI] İstek esnasında sistemsel hata oluştu: {e}")
        return None

def canli_bakiye_sorgula():
    """Testnet cüzdanındaki USDT bakiyesini garantili olarak çeker."""
    bakiye_verisi = binance_imzali_talep("GET", "/fapi/v2/balance")
    print(f"📡 [BORSADAN GELEN HAM BAKİYE YANITI]: {bakiye_verisi}")
    
    if bakiye_verisi and isinstance(bakiye_verisi, list):
        for varlik in bakiye_verisi:
            if varlik.get("asset") == "USDT":
                kullanilabilir_usdt = float(varlik.get("availableBalance", 0.0))
                print(f"💰 [CÜZDAN BİLGİSİ] Net Kullanılabilir Bakiyeniz: {kullanilabilir_usdt} USDT")
                return kullanilabilir_usdt
                
    print("⚠️ [CÜZDAN UYARISI] Borsa yanıtından USDT varlığı ayrıştırılamadı, bakiye 0.0 kabul ediliyor.")
    return 0.0


def canli_kaldirac_ayarla(sembol, kaldirac_orani):
    binance_imzali_talep("POST", "/fapi/v1/leverage", {"symbol": f"{sembol}USDT", "leverage": kaldirac_orani})

def canli_market_emri_gonder(sembol, yon, miktar):
    """Ana pozisyonu açmak için piyasa emri gönderir. Adet hassasiyeti tam sayıya çekildi."""
    side = "BUY" if yon == "LONG" else "SELL"
    guncel_miktar = round(miktar, 0) if miktar >= 1 else round(miktar, 1)
    if guncel_miktar == 0: guncel_miktar = 1.0
    
    return binance_imzali_talep("POST", "/fapi/v1/order", {
        "symbol": f"{sembol}USDT", 
        "side": side, 
        "type": "MARKET", 
        "quantity": guncel_miktar
    })

# ==========================================
# 📊 VERİ ANALİZİ İÇİN İNDİKATÖR MOTORLARI
# ==========================================
def ema_hesapla(veriler, periyot=21):
    if len(veriler) < periyot: return veriler[-1]
    k = 2 / (periyot + 1)
    ema = sum(veriler[:periyot]) / periyot
    for fiyat in veriler[periyot:]: ema = (fiyat * k) + (ema * (1 - k))
    return ema

def market_makro_trend_oku():
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        res = requests.get(url, params={"symbol": "BTCUSDT", "interval": "4h", "limit": 30}, timeout=5)
        if res.status_code != 200: return "NOTR"
        hacimler = [float(mum[5]) for mum in res.json()]
        guncel_ema = ema_hesapla(hacimler, 21)
        return "POZITIF" if hacimler[-1] >= guncel_ema else "NEGATIF"
    except: return "NOTR"

def market_mikro_trend_oku():
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        res = requests.get(url, params={"symbol": "BTCUSDT", "interval": "1h", "limit": 2}, timeout=5)
        if res.status_code != 200: return "NOTR"
        son_mum = res.json()[-2]
        açılış, kapanış = float(son_mum[1]), float(son_mum[4])
        return "LONG" if kapanış >= açılış else "SHORT"
    except: return "NOTR"

def binance_rsi_hesapla(sembol, periyot=14):
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        res = requests.get(url, params={"symbol": f"{sembol}USDT", "interval": "5m", "limit": periyot + 30}, timeout=5)
        if res.status_code != 200: return 50.0
        kapanislar = [float(mum[4]) for mum in res.json()]
        if len(kapanislar) < periyot: return 50.0
        
        kayiplar, kazanclar = [], []
        for i in range(1, len(kapanislar)):
            fark = kapanislar[i] - kapanislar[i-1]
            kazanclar.append(fark if fark > 0 else 0)
            kayiplar.append(abs(fark) if fark < 0 else 0)
            
        ort_kazanc = sum(kazanclar[:periyot]) / periyot
        ort_kayip = sum(kayiplar[:periyot]) / periyot
        for i in range(periyot, len(kazanclar)):
            ort_kazanc = (ort_kazanc * (periyot - 1) + kazanclar[i]) / periyot
            ort_kayip = (ort_kayip * (periyot - 1) + kayiplar[i]) / periyot
            
        if ort_kayip == 0: return 100.0
        return round(100 - (100 / (100 + (ort_kazanc / ort_kayip))), 2)
    except: return 50.0

# ==========================================
# 📢 TELEGRAM VE LABORATUVAR RAPORLAMA MOTORU
# ==========================================
def telegram_mesaj_gonder(mesaj):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def optimizasyon_raporuna_yaz(sembol, yon, giris, sl, tp, matris_puan, rsi, makro, mikro):
    dosya_adi = "backtest_raporu.csv"
    dosya_exists = os.path.isfile(dosya_adi)
    with open(dosya_adi, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not dosya_exists:
            writer.writerow(["Zaman", "Sembol", "Yön", "Giriş Fiyatı", "Hedef SL", "Hedef TP", "Matris Skoru", "O Anki RSI", "BTC Makro Hacim", "BTC Mikro Trend"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sembol, yon, giris, sl, tp, matris_puan, rsi, makro, mikro])

# ==========================================
# 🔄 HAFIZA SENKRONİZASYON MOTORU
# ==========================================
def borsa_aktif_pozisyonlarini_hafizaya_yenile():
    global aktif_pozisyonlar
    pozisyonlar = binance_imzali_talep("GET", "/fapi/v2/positionRisk")
    yeni_hafiza = {}
    if pozisyonlar and isinstance(pozisyonlar, list):
        for poz in pozisyonlar:
            miktar = float(poz.get("positionAmt", 0.0))
            if miktar != 0:
                sembol = poz.get("symbol", "").replace("USDT", "").strip().upper()
                if sembol not in KARA_LISTE:
                    yeni_hafiza[sembol] = {"adet": abs(miktar)}
    aktif_pozisyonlar = yeni_hafiza

# ==========================================
# 🌐 AV MOTORU: TAM ŞEFFAF LOG KATMANLI SÜRÜM
# ==========================================
def coinank_piyasasini_kazi(binance_onayli_koinler):
    borsa_aktif_pozisyonlarini_hafizaya_yenile()
    havuz_boyutu = len(aktif_pozisyonlar)
    
    print("\n" + "="*60)
    print(f"⏰ TARAMA ZAMANI: {datetime.now().strftime('%H:%M:%S')}")
    print(f"📊 [CÜZDAN/HAVUZ] Aktif Açık Pozisyon Sayısı: {havuz_boyutu}/10")
    
    makro_durum = market_makro_trend_oku()  
    mikro_durum = market_mikro_trend_oku()  
    print(f"🌍 [PİYASA ANALİZİ] BTC Makro Hacim Trendi: {makro_durum} | BTC 1h Mikro Trend: {mikro_durum}")
    print("="*60)

    url = "https://coinank.com/longshort/realtime"

    market_kurallari = {}
    try:
        res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        if res.status_code == 200:
            for sym in res.json()["symbols"]:
                market_kurallari[sym["symbol"].upper()] = {
                    "p_prec": int(sym.get("pricePrecision", 2)),
                    "q_prec": int(sym.get("quantityPrecision", 0))
                }
    except:
        pass
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            def resim_engelle(route):
                try: route.abort()
                except: pass
            
            page.route("**/*.{png,jpg,jpeg,svg,gif,webp}", resim_engelle)
            page.goto(url, timeout=60000)
            page.wait_for_selector("tbody tr.ant-table-row", timeout=60000)
            time.sleep(5)
            
            try:
                page.locator("div.ant-select-selector").last.click()
                time.sleep(1)
                page.locator("div.ant-select-item-option-content:has-text('100')").click()
                time.sleep(3)
            except: pass

            tablo_verisi = page.evaluate("""() => Array.from(document.querySelectorAll("tbody tr.ant-table-row")).map(r => ({ text: r.innerText || "" }))""")
            
            if not tablo_verisi:
                print("❌ [HATA] CoinAnk web sayfasından hiçbir satır okunamadı!")
                return

            for veri in tablo_verisi:
                try:
                    satir_parcalari = [m.strip() for m in veri["text"].split("\n") if m.strip()]
                    if not satir_parcalari: continue
                    sembol = satir_parcalari[0].replace("USDT", "").replace("-", "").strip().upper()
                    # 📌 DÜZELTME: CoinAnk'taki tek harfli "B" kısaltmasını Binance standartı "BTC"ye çeviriyoruz
                    if sembol == "B":
                        sembol = "BTC"

                    if not sembol.isalnum() or not sembol.isascii(): continue
                    
                    yuzdeler = []
                    for parca in satir_parcalari:
                        temiz = parca.replace("%", "").strip()
                        if "%" in parca or ("." in temiz and temiz.replace(".","").replace("-","").replace("+","").isdigit()):
                            try: yuzdeler.append(float(temiz))
                            except: pass

                    if len(yuzdeler) < 9: 
                        print(f"⚠️ [EKSİK VERİ] #{sembol} için yeterli yüzde verisi kazınamadı.")
                        continue
                        
                    l_30m, s_30m, l_1h, s_1h = yuzdeler[5], yuzdeler[6], yuzdeler[7], yuzdeler[8]
                    
                    is_alarm, islem_yonu, coinank_skor = False, "", 0.0
                    if l_30m >= 60.0 and l_1h >= 58.0: 
                        is_alarm, islem_yonu, coinank_skor = True, "LONG", l_30m
                    elif s_30m >= 60.0 and s_1h >= 58.0: 
                        is_alarm, islem_yonu, coinank_skor = True, "SHORT", s_30m

                    log_on_taki = f"🔍 [İNCELEME] #{sembol:<7} -> 30m[L:%{l_30m:.1f} S:%{s_30m:.1f}] | 1h[L:%{l_1h:.1f} S:%{s_1h:.1f}]"
                    
                    if not is_alarm:
                        print(f"{log_on_taki} -> [X] Sinyal Eşiği Yetersiz.")
                        continue
                    
                    zaten_acik = sembol in aktif_pozisyonlar
                    onayli_mi = sembol in binance_onayli_koinler
                    kara_listede_mi = sembol in KARA_LISTE
                    
                    if kara_listede_mi or zaten_acik or (not onayli_mi) or len(aktif_pozisyonlar) >= 10:
                        sebeb = ""
                        if kara_listede_mi: sebeb = "Kara Listede"
                        elif zaten_acik: sebeb = "Zaten Açık Pozisyon Var"
                        elif not onayli_mi: sebeb = "Binance Vadeli Listesinde Yok"
                        elif len(aktif_pozisyonlar) >= 10: sebeb = "Havuz Tamamen Dolu (10/10)"
                        print(f"{log_on_taki} -> ⭐ SİNYAL VAR ({islem_yonu}) Fakat [ENGELLENDİ: {sebeb}]")
                        continue

                    print(f"{log_on_taki} -> 🔥 [KRİTİK ONAY] Sinyal Geçti! {islem_yonu} emri hazırlanıyor...")
                    
                    toplam_matris_puani = 0
                    if makro_durum == "POZITIF" and islem_yonu == "LONG": toplam_matris_puani += 20
                    elif makro_durum == "NEGATIF" and islem_yonu == "SHORT": toplam_matris_puani += 20
                    if mikro_durum == islem_yonu: toplam_matris_puani += 25
                    if coinank_skor >= 65.0: toplam_matris_puani += 35
                    elif coinank_skor >= 60.0: toplam_matris_puani += 20
                    
                    rsi_degeri = binance_rsi_hesapla(sembol)
                    if islem_yonu == "LONG" and rsi_degeri < 70.0: toplam_matris_puani += 20
                    elif islem_yonu == "SHORT" and rsi_degeri > 30.0: toplam_matris_puani += 20
                    
                    anlik_fiyat = binance_canli_fiyat_al(sembol)
                    canli_bakiye = canli_bakiye_sorgula()
                    
                    if anlik_fiyat and canli_bakiye >= POZISYON_MARJIN:
                        koin_adedi = (POZISYON_MARJIN * KALDIRAC) / anlik_fiyat
                        canli_kaldirac_ayarla(sembol, KALDIRAC)
                        
                        # Ana Emri Gönder
                        borsa_onayi = canli_market_emri_gonder(sembol, islem_yonu, koin_adedi)
                            
                        if borsa_onayi and "orderId" in borsa_onayi:
                            gercek_giris = float(borsa_onayi.get("avgPrice", anlik_fiyat))
                            if gercek_giris == 0: gercek_giris = float(borsa_onayi.get("price", anlik_fiyat))
               

                            kural = market_kurallari.get(f"{sembol}USDT", {"p_prec": 2, "q_prec": 0})
                            p_prec = kural["p_prec"]
    
                            # 📌 FİZİKİ HEDEFLER
                            if islem_yonu == "LONG":
                                sl_fiyat = round(gercek_giris * (1 - 0.010), p_prec)
                                tp_fiyat = round(gercek_giris * (1 + 0.012), p_prec)
                                ters_side = "SELL"
                            else:
                                sl_fiyat = round(gercek_giris * (1 + 0.010), p_prec)
                                tp_fiyat = round(gercek_giris * (1 - 0.012), p_prec)
                                ters_side = "BUY"
                            

# 🎯 NİHAİ KARARLI YAPI: Testnet Engellerini Aşan ve Tıkır Tıkır Çalışan Algo Mimari
                            try:
                                q_prec = kural["q_prec"]
                                temiz_adet = round(koin_adedi, q_prec)

                                # --------------------------------------------------
                                # STEP 1: ALGO TAKE PROFIT (TP) EMİR GÖNDERİMİ
                                # --------------------------------------------------
                                tp_algo_paketi = {
                                    "symbol": f"{sembol}USDT",
                                    "side": ters_side,
                                    "algoType": "TAKE_PROFIT_MARKET",
                                    "qty": str(temiz_adet),
                                    "stopPrice": str(tp_fiyat),
                                    "reduceOnly": "true",
                                    "workingType": "MARK_PRICE"
                                }
                                
                                print(f"📡 [ALGO NİHAİ - BORSAYA GİDEN TP PAKETİ]: {tp_algo_paketi}")
                                tp_onay = binance_imzali_talep("POST", "/sapi/v1/algo/futures/newOrderAlgo", tp_algo_paketi)
                                print(f"📥 [ALGO NİHAİ - BORSADAN GELEN TP YANITI]: {tp_onay}")
                                
                                time.sleep(0.5)
                                
                                # --------------------------------------------------
                                # STEP 2: ALGO STOP LOSS (SL) EMİR GÖNDERİMİ
                                # --------------------------------------------------
                                sl_algo_paketi = {
                                    "symbol": f"{sembol}USDT",
                                    "side": ters_side,
                                    "algoType": "STOP_MARKET",
                                    "qty": str(temiz_adet),
                                    "stopPrice": str(sl_fiyat),
                                    "reduceOnly": "true",
                                    "workingType": "MARK_PRICE"
                                }
                                
                                print(f"📡 [ALGO NİHAİ - BORSAYA GİDEN SL PAKETİ]: {sl_algo_paketi}")
                                sl_onay = binance_imzali_talep("POST", "/sapi/v1/algo/futures/newOrderAlgo", sl_algo_paketi)
                                print(f"📥 [ALGO NİHAİ - BORSADAN GELEN SL YANITI]: {sl_onay}")
                                
                                # --------------------------------------------------
                                # STEP 3: RAPORLAMA VE KAYIT
                                # --------------------------------------------------
                                optimizasyon_raporuna_yaz(sembol, islem_yonu, gercek_giris, sl_fiyat, tp_fiyat, toplam_matris_puani, rsi_degeri, makro_durum, mikro_durum)
                                aktif_pozisyonlar[sembol] = {"adet": koin_adedi}
                                
                                telegram_mesaj_gonder(f"🔬 *[LABORATUVAR EMİR AÇILDI]*\nSembol: #{sembol}\nYön: {islem_yonu}\nGiriş: {gercek_giris}\n🎯 TP: {tp_fiyat} | 🛑 SL: {sl_fiyat}\n📌 _Durum: Algo Emirleri Aktif_")
                                print(f"🚀 [ BAŞARILI ] #{sembol} {islem_yonu} pozisyonu ve Algo TP/SL süreçleri sorunsuz tamamlandı.")
                                
                            except Exception as emir_hatasi:
                                print(f"❌ [ALGO NİHAİ SİSTEM HATASI]: {emir_hatasi}")




                except Exception as ic_hata:
                    print(f"⚠️ [SATIR HATASI] Bir koin işlenirken iç hata oluştu: {ic_hata}")
                    continue

        except Exception as ana_e:
            print(f"❌ [DÖNGÜ HATASI] Tarayıcı kazıma motoru hatası: {ana_e}")
        finally: 
            browser.close()
            print("="*60 + "\n")

def binance_vadeli_koinleri_getir():
    try:
        res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        if res.status_code == 200:
            return {sym["baseAsset"].upper() for sym in res.json()["symbols"] if sym.get("status") == "TRADING" and sym.get("symbol", "").endswith("USDT") and sym.get("underlyingType") == "COIN"}
    except: return set()

def binance_canli_fiyat_al(sembol):
    try:
        res = requests.get("https://fapi.binance.com/fapi/v1/ticker/price", params={"symbol": f"{sembol}USDT"}, timeout=3)
        if res.status_code == 200: return float(res.json()["price"])
    except: return None

# ==========================================
# 🚀 MAIN RUNNER
# ==========================================
def ana_dongu():
    print("🔬 Şeffaf Log Destekli Laboratuvar Botu Başlatıldı!")
    while True:
        binance_onayli_koinler = binance_vadeli_koinleri_getir()
        if binance_onayli_koinler: coinank_piyasasini_kazi(binance_onayli_koinler)
        time.sleep(TARAMA_ARALIGI)

if __name__ == "__main__":
    ana_dongu()
