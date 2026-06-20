# --- API BİLGİLERİ ---
# Testnet kullanıyorsanız Testnet anahtarlarını ve URL'sini, 
# Gerçek hesapta deneyecekseniz gerçek anahtarları ve URL'yi yazın.


import time
from binance import Client


# Eğer Testnet kullanıyorsanız testnet=True bırakın, Gerçek hesap için False yapın
client = Client(API_KEY, SECRET_KEY, testnet=True)

def btc_python_binance_testi():
    symbol = "BTCUSDT"
    
    # --- 1. ADIM: RESMİ KÜTÜPHANE İLE MARKET LONG POZİSYONU AÇMA ---
    print(f"🔄 1. ADIM: {symbol} için piyasa fiyatından deneme amaçlı LONG pozisyonu açılıyor...")
    try:
        ana_emir = client.futures_create_order(
            symbol=symbol,
            side='BUY',
            type='MARKET',
            quantity=0.001
        )
        print(f"📥 Ana Pozisyon Yanıtı: {ana_emir}")
    except Exception as e:
        print(f"❌ Ana pozisyon açılırken hata oluştu: {e}")
        return

    # Canlı fiyatı kütüphane üzerinden çekiyoruz
    try:
        ticker = client.futures_symbol_ticker(symbol=symbol)
        giris_fiyati = float(ticker['price'])
        print(f"✅ Pozisyon Başarıyla Açıldı. Giriş Fiyatı: {giris_fiyati} USDT")
    except Exception:
        giris_fiyati = 63000.0  # Hata durumunda güvenli taban fiyat
        
    # %1 TP ve %1 SL seviyelerini hesapla
    tp_tetik_fiyati = round(giris_fiyati * 1.01, 2)
    sl_tetik_fiyati = round(giris_fiyati * 0.99, 2)
    print(f"📊 Hedefler -> TP (Kar Al): {tp_tetik_fiyati} | SL (Stop): {sl_tetik_fiyati}")
    
    # --- 2. ADIM: RESMİ KÜTÜPHANE STANDARTLARINDA TAKE PROFIT MARKET EMRİ ---
    print(f"\n🔄 2. ADIM: {symbol} için TAKE_PROFIT_MARKET emri gönderiliyor...")
    try:
        tp_emir = client.futures_create_order(
            symbol=symbol,
            side='SELL',                         # LONG'u kapatmak için ters yön
            type='TAKE_PROFIT_MARKET',           # Dokümandaki resmi tip
            stopPrice=str(tp_tetik_fiyati),      # Tetik fiyatı (String olmalı)
            workingType='MARK_PRICE',            # Grafik fiyatı tetiklemesi
            closePosition='true'                 # Miktar göndermeden tüm pozisyonu kapatır
        )
        print(f"📥 TP Emri Başarıyla Alındı: {tp_emir}")
    except Exception as e:
        print(f"❌ TP Emri Hatası: {e}")
        
    # --- 3. ADIM: RESMİ KÜTÜPHANE STANDARTLARINDA STOP MARKET EMRİ ---
    print(f"\n🔄 3. ADIM: {symbol} için STOP_MARKET emri gönderiliyor...")
    try:
        sl_emir = client.futures_create_order(
            symbol=symbol,
            side='SELL',                         # LONG'u kapatmak için ters yön
            type='STOP_MARKET',                  # Dokümandaki resmi tip
            stopPrice=str(sl_tetik_fiyati),      # Stop fiyatı (String olmalı)
            workingType='MARK_PRICE',            # Grafik fiyatı tetiklemesi
            closePosition='true'                 # Miktar göndermeden tüm pozisyonu kapatır
        )
        print(f"📥 SL Emri Başarıyla Alındı: {sl_emir}")
    except Exception as e:
        print(f"❌ SL Emri Hatası: {e}")

if __name__ == "__main__":
    btc_python_binance_testi()
