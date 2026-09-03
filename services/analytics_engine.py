import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import Dict, List
from models import db, Stock, StockPrice, StockComparison, AnalyticsReport

logger = logging.getLogger(__name__)

class AnalyticsEngine:
    """Generate analytics, comparisons, and performance metrics"""
    
    def calculate_returns(self, symbol: str, period_days: int = 30) -> Dict:
        """Calculate returns for different periods"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                return {}
            
            prices = StockPrice.query.filter_by(stock_id=stock.id).order_by(
                StockPrice.date.desc()
            ).limit(period_days).all()
            
            if len(prices) < 2:
                return {}
            
            prices = list(reversed(prices))
            start_price = prices[0].close_price
            end_price = prices[-1].close_price
            
            daily_returns = []
            for i in range(1, len(prices)):
                ret = (prices[i].close_price - prices[i-1].close_price) / prices[i-1].close_price
                daily_returns.append(ret)
            
            total_return = (end_price - start_price) / start_price
            
            return {
                'daily_return': np.mean(daily_returns) if daily_returns else 0,
                'total_return': total_return,
                'volatility': np.std(daily_returns) if daily_returns else 0,
                'sharpe_ratio': self.calculate_sharpe_ratio(daily_returns),
                'max_drawdown': self.calculate_max_drawdown(prices)
            }
        except Exception as e:
            logger.error(f"Error calculating returns for {symbol}: {str(e)}")
            return {}
    
    def calculate_sharpe_ratio(self, returns: List[float], risk_free_rate: float = 0.02) -> float:
        """Calculate Sharpe ratio"""
        if not returns or np.std(returns) == 0:
            return 0
        
        annual_return = np.mean(returns) * 252
        annual_volatility = np.std(returns) * np.sqrt(252)
        
        return (annual_return - risk_free_rate) / annual_volatility if annual_volatility != 0 else 0
    
    def calculate_max_drawdown(self, prices: List) -> float:
        """Calculate maximum drawdown"""
        if not prices:
            return 0
        
        price_values = [p.close_price for p in prices]
        cumulative_max = np.maximum.accumulate(price_values)
        drawdown = (np.array(price_values) - cumulative_max) / cumulative_max
        
        return float(np.min(drawdown)) if len(drawdown) > 0 else 0
    
    def generate_analytics_report(self, symbol: str) -> Dict:
        """Generate comprehensive analytics report"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                return {}
            
            # Calculate returns for different periods
            daily_returns = self.calculate_returns(symbol, 1)
            weekly_returns = self.calculate_returns(symbol, 7)
            monthly_returns = self.calculate_returns(symbol, 30)
            yearly_returns = self.calculate_returns(symbol, 365)
            
            latest_price = StockPrice.query.filter_by(stock_id=stock.id).order_by(
                StockPrice.date.desc()
            ).first()
            
            if not latest_price:
                return {}
            
            # Store report in database
            report = AnalyticsReport(
                stock_id=stock.id,
                daily_return=daily_returns.get('total_return', 0),
                weekly_return=weekly_returns.get('total_return', 0),
                monthly_return=monthly_returns.get('total_return', 0),
                yearly_return=yearly_returns.get('total_return', 0),
                volatility_daily=daily_returns.get('volatility', 0),
                volatility_annual=yearly_returns.get('volatility', 0),
                sharpe_ratio=yearly_returns.get('sharpe_ratio', 0),
                max_drawdown=yearly_returns.get('max_drawdown', 0),
                target_price_12m=latest_price.close_price * 1.15,  # 15% upside target
                upside_potential=15.0,
                recommendation='BUY' if yearly_returns.get('sharpe_ratio', 0) > 1 else 'HOLD'
            )
            
            db.session.add(report)
            db.session.commit()
            
            return {
                'symbol': symbol,
                'daily_return': daily_returns.get('total_return', 0),
                'weekly_return': weekly_returns.get('total_return', 0),
                'monthly_return': monthly_returns.get('total_return', 0),
                'yearly_return': yearly_returns.get('total_return', 0),
                'volatility_daily': daily_returns.get('volatility', 0),
                'volatility_annual': yearly_returns.get('volatility', 0),
                'sharpe_ratio': yearly_returns.get('sharpe_ratio', 0),
                'max_drawdown': yearly_returns.get('max_drawdown', 0),
                'current_price': latest_price.close_price,
                'target_price': latest_price.close_price * 1.15,
                'recommendation': 'BUY' if yearly_returns.get('sharpe_ratio', 0) > 1 else 'HOLD'
            }
        except Exception as e:
            logger.error(f"Error generating analytics for {symbol}: {str(e)}")
            return {}
    
    def compare_stocks(self, symbol1: str, symbol2: str, period_days: int = 30) -> Dict:
        """Compare two stocks"""
        try:
            stock1 = Stock.query.filter_by(symbol=symbol1).first()
            stock2 = Stock.query.filter_by(symbol=symbol2).first()
            
            if not stock1 or not stock2:
                return {}
            
            # Get historical prices
            prices1 = StockPrice.query.filter_by(stock_id=stock1.id).filter(
                StockPrice.date >= datetime.utcnow() - timedelta(days=period_days)
            ).order_by(StockPrice.date).all()
            
            prices2 = StockPrice.query.filter_by(stock_id=stock2.id).filter(
                StockPrice.date >= datetime.utcnow() - timedelta(days=period_days)
            ).order_by(StockPrice.date).all()
            
            if not prices1 or not prices2:
                return {}
            
            # Calculate returns
            returns1 = [(prices1[i].close_price - prices1[i-1].close_price) / prices1[i-1].close_price 
                       for i in range(1, len(prices1))]
            returns2 = [(prices2[i].close_price - prices2[i-1].close_price) / prices2[i-1].close_price 
                       for i in range(1, len(prices2))]
            
            # Calculate correlation
            correlation = np.corrcoef(returns1, returns2)[0, 1] if len(returns1) > 1 else 0
            
            # Performance comparison
            perf1 = (prices1[-1].close_price - prices1[0].close_price) / prices1[0].close_price
            perf2 = (prices2[-1].close_price - prices2[0].close_price) / prices2[0].close_price
            
            # Volatility comparison
            vol1 = np.std(returns1)
            vol2 = np.std(returns2)
            
            comparison = StockComparison(
                stock1_id=stock1.id,
                stock2_id=stock2.id,
                correlation=float(correlation),
                beta=float(perf1 / perf2) if perf2 != 0 else 0,
                performance_ratio=float(perf1 - perf2),
                volatility_ratio=float(vol1 - vol2),
                period_days=period_days
            )
            
            db.session.add(comparison)
            db.session.commit()
            
            return {
                'symbol1': symbol1,
                'symbol2': symbol2,
                'correlation': float(correlation),
                'beta': float(perf1 / perf2) if perf2 != 0 else 0,
                'performance_1': perf1,
                'performance_2': perf2,
                'performance_difference': perf1 - perf2,
                'volatility_1': vol1,
                'volatility_2': vol2,
                'volatility_difference': vol1 - vol2,
                'period_days': period_days
            }
        except Exception as e:
            logger.error(f"Error comparing stocks {symbol1} and {symbol2}: {str(e)}")
            return {}
    
    def compare_all_stocks(self, symbols: List[str], period_days: int = 30) -> List[Dict]:
        """Compare all stocks with each other"""
        comparisons = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                comp = self.compare_stocks(symbols[i], symbols[j], period_days)
                if comp:
                    comparisons.append(comp)
        
        return comparisons
    
    def get_price_targets(self, symbol: str) -> Dict:
        """Generate price targets based on technical and fundamental analysis"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                return {}
            
            latest_price = StockPrice.query.filter_by(stock_id=stock.id).order_by(
                StockPrice.date.desc()
            ).first()
            
            if not latest_price:
                return {}
            
            current_price = latest_price.close_price
            
            # Conservative target (80% upside)
            conservative_target = current_price * 1.08
            
            # Base target (15% upside)
            base_target = current_price * 1.15
            
            # Bullish target (25% upside)
            bullish_target = current_price * 1.25
            
            # Support and resistance
            prices = StockPrice.query.filter_by(stock_id=stock.id).order_by(
                StockPrice.date.desc()
            ).limit(50).all()
            
            all_prices = [p.close_price for p in prices]
            support = min(all_prices)
            resistance = max(all_prices)
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'support': support,
                'resistance': resistance,
                'conservative_target': conservative_target,
                'base_target': base_target,
                'bullish_target': bullish_target,
                'upside_to_base': ((base_target - current_price) / current_price) * 100,
                'downside_to_support': ((current_price - support) / current_price) * 100
            }
        except Exception as e:
            logger.error(f"Error calculating price targets for {symbol}: {str(e)}")
            return {}
